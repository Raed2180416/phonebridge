"""Mirror phone notifications into desktop notification center with 2-way dismissal sync."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time

from backend.state import state
from backend.kdeconnect import KDEConnect

try:
    import dbus  # type: ignore
except Exception:  # pragma: no cover
    dbus = None

log = logging.getLogger(__name__)


def _content_hash(payload: dict) -> str:
    """Stable hash over the fields that affect desktop notification appearance."""
    stable = {
        "app": str(payload.get("app") or ""),
        "title": str(payload.get("title") or ""),
        "text": str(payload.get("text") or ""),
        "actions": sorted(str(x) for x in (payload.get("actions") or [])),
        "replyId": str(payload.get("replyId") or ""),
    }
    return hashlib.md5(json.dumps(stable, sort_keys=True).encode()).hexdigest()


class NotificationMirror:
    def __init__(self):
        self._lock = threading.RLock()
        self._bus = None
        self._iface = None
        self._signal_connected = False
        self._phone_to_desktop: dict[str, int] = {}
        self._desktop_to_phone: dict[int, str] = {}
        self._phone_payload: dict[str, dict] = {}
        self._phone_hash: dict[str, str] = {}  # phone_id -> last posted content hash
        self._closing_desktop_ids: set[int] = set()
        self._kc = KDEConnect()

    def _ensure_ready(self) -> bool:
        if dbus is None:
            return False
        if self._iface is not None and self._bus is not None:
            return True
        try:
            self._bus = dbus.SessionBus()
            obj = self._bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
            self._iface = dbus.Interface(obj, "org.freedesktop.Notifications")
            if not self._signal_connected:
                self._bus.add_signal_receiver(
                    self._on_notification_closed,
                    signal_name="NotificationClosed",
                    dbus_interface="org.freedesktop.Notifications",
                )
                self._bus.add_signal_receiver(
                    self._on_action_invoked,
                    signal_name="ActionInvoked",
                    dbus_interface="org.freedesktop.Notifications",
                )
                self._bus.add_signal_receiver(
                    self._on_notification_replied,
                    signal_name="NotificationReplied",
                    dbus_interface="org.freedesktop.Notifications",
                )
                self._signal_connected = True
            return True
        except Exception as exc:
            log.warning("Notification mirror unavailable: %s", exc)
            self._bus = None
            self._iface = None
            return False

    def sync(self, notifications: list[dict]) -> None:
        if not self._ensure_ready():
            return
        incoming = {}
        for row in list(notifications or []):
            nid = str((row or {}).get("id") or "").strip()
            if nid:
                incoming[nid] = row or {}

        with self._lock:
            stale = [nid for nid in self._phone_to_desktop.keys() if nid not in incoming]
        for nid in stale:
            self.close_for_phone(nid)

        for nid, payload in incoming.items():
            self._upsert_one(nid, payload)

    def _upsert_one(self, phone_id: str, payload: dict) -> None:
        if self._iface is None:
            return
        new_hash = _content_hash(payload)
        with self._lock:
            replace_id = int(self._phone_to_desktop.get(phone_id, 0) or 0)
            # If this notification already has a desktop presence AND content unchanged, skip.
            if replace_id and self._phone_hash.get(phone_id) == new_hash:
                return
        summary = str(payload.get("title") or payload.get("app") or "Phone Notification")
        body = str(payload.get("text") or "")
        app_name = str(payload.get("app") or "Phone")
        hints = {
            "desktop-entry": "phonebridge",
            "transient": dbus.Boolean(False),
            "resident": dbus.Boolean(False),
            "x-phonebridge-id": dbus.String(phone_id),
        }
        actions = self._normalize_actions(payload)
        try:
            notif_id = int(
                self._iface.Notify(
                    "PhoneBridge",
                    dbus.UInt32(replace_id),
                    "phonebridge",
                    f"{app_name} · {summary}",
                    body,
                    dbus.Array(actions, signature="s"),
                    dbus.Dictionary(hints, signature="sv"),
                    dbus.Int32(-1),
                )
            )
        except Exception as exc:
            log.warning("Notification mirror publish failed: %s", exc)
            return

        with self._lock:
            prev = self._phone_to_desktop.get(phone_id)
            if prev and int(prev) != notif_id:
                self._desktop_to_phone.pop(int(prev), None)
            self._phone_to_desktop[phone_id] = notif_id
            self._desktop_to_phone[notif_id] = phone_id
            self._phone_hash[phone_id] = new_hash
            self._phone_payload[phone_id] = dict(payload or {})

    @staticmethod
    def _normalize_actions(payload: dict) -> list[str]:
        p = dict(payload or {})
        raw = list(p.get("actions") or [])
        # Already freedesktop style: [key, label, key, label...]
        if len(raw) % 2 == 0 and all(isinstance(x, str) for x in raw):
            out: list[str] = [str(x) for x in raw]
        else:
            out = []
            for entry in raw:
                if isinstance(entry, dict):
                    key = str(entry.get("key") or entry.get("id") or "").strip()
                    label = str(entry.get("label") or entry.get("title") or key).strip()
                    if key:
                        out.extend([key, label or key])
                    continue
                key = str(entry or "").strip()
                if not key:
                    continue
                label = key.replace("_", " ").replace("-", " ").strip().title()
                out.extend([key, label or key])
        # Fallback actions for notifications that don't expose rich actions.
        if str(p.get("replyId") or "").strip():
            out.extend(["__pb_reply", "Reply"])
        text = str(p.get("text") or p.get("title") or "").strip()
        if text:
            out.extend(["__pb_copy", "Copy"])
        return out

    def close_for_phone(self, phone_id: str) -> None:
        if self._iface is None and not self._ensure_ready():
            return
        with self._lock:
            desktop_id = self._phone_to_desktop.pop(str(phone_id), None)
            if desktop_id is None:
                return
            self._desktop_to_phone.pop(int(desktop_id), None)
            self._phone_payload.pop(str(phone_id), None)
            self._phone_hash.pop(str(phone_id), None)
            self._closing_desktop_ids.add(int(desktop_id))
        try:
            self._iface.CloseNotification(dbus.UInt32(int(desktop_id)))
        except Exception:
            pass

    def clear(self) -> None:
        with self._lock:
            ids = list(self._phone_to_desktop.keys())
        for pid in ids:
            self.close_for_phone(pid)

    def _on_notification_closed(self, desktop_id, reason):
        try:
            did = int(desktop_id)
            why = int(reason)
        except Exception:
            return

        with self._lock:
            if did in self._closing_desktop_ids:
                self._closing_desktop_ids.discard(did)
                return
            phone_id = self._desktop_to_phone.pop(did, None)
            if phone_id:
                self._phone_to_desktop.pop(phone_id, None)
                self._phone_payload.pop(phone_id, None)
                self._phone_hash.pop(phone_id, None)

        if not phone_id:
            return
        # 2 = dismissed by user, 1 = expired by server timeout.
        if why not in {1, 2}:
            return
        try:
            self._kc.dismiss_notification(phone_id)
        except Exception:
            pass
        rows = list(state.get("notifications", []) or [])
        rows = [r for r in rows if str((r or {}).get("id") or "") != str(phone_id)]
        state.set("notifications", rows)
        state.set("notif_revision", {"id": str(phone_id), "updated_at": int(time.time() * 1000)})

    def _on_action_invoked(self, desktop_id, action_key):
        try:
            did = int(desktop_id)
        except Exception:
            return
        action = str(action_key or "").strip()
        if not action:
            return
        with self._lock:
            phone_id = self._desktop_to_phone.get(did)
            payload = dict(self._phone_payload.get(str(phone_id), {}) if phone_id else {})
        if not phone_id:
            return
        if action == "__pb_copy":
            self._copy_notification_text(payload)
            return
        if action == "__pb_reply":
            # Open native quick-reply flow on phone app if supported by source app.
            try:
                self._kc.open_notification_reply(str(phone_id))
            except Exception:
                pass
            return
        try:
            self._kc.send_notification_action(str(phone_id), action)
        except Exception:
            pass
        state.set("notif_revision", {"id": str(phone_id), "updated_at": int(time.time() * 1000)})

    def _on_notification_replied(self, desktop_id, reply_text):
        try:
            did = int(desktop_id)
        except Exception:
            return
        text = str(reply_text or "").strip()
        if not text:
            return
        with self._lock:
            phone_id = self._desktop_to_phone.get(did)
            payload = dict(self._phone_payload.get(str(phone_id), {}) if phone_id else {})
        if not phone_id:
            return
        reply_id = str(payload.get("replyId") or "").strip()
        if not reply_id:
            return
        try:
            self._kc.reply_notification(reply_id, text)
        except Exception:
            pass

    @staticmethod
    def _copy_notification_text(payload: dict):
        text = str(payload.get("text") or payload.get("title") or "").strip()
        if not text:
            return
        try:
            from PyQt6.QtWidgets import QApplication  # type: ignore
            app = QApplication.instance()
            if app is not None:
                app.clipboard().setText(text)
                return
        except Exception:
            pass
        if os.environ.get("WAYLAND_DISPLAY"):
            try:
                subprocess.run(["wl-copy"], input=text, text=True, check=False, timeout=2)
                return
            except Exception:
                pass
        try:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=False, timeout=2)
        except Exception:
            pass


_MIRROR: NotificationMirror | None = None
_LOCK = threading.Lock()


def _instance() -> NotificationMirror:
    global _MIRROR
    with _LOCK:
        if _MIRROR is None:
            _MIRROR = NotificationMirror()
        return _MIRROR


def sync_desktop_notifications(notifications: list[dict]) -> None:
    _instance().sync(notifications)


def close_phone_notification(phone_id: str) -> None:
    _instance().close_for_phone(phone_id)


def clear_phone_notifications() -> None:
    _instance().clear()
