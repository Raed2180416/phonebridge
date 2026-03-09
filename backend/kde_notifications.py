"""Notification and suppression helpers for the KDE Connect facade."""

from __future__ import annotations

import configparser
import io
import shutil
import subprocess
from pathlib import Path


def get_notifications(kc):
    try:
        iface = kc._iface(kc._dev("notifications"), "org.kde.kdeconnect.device.notifications")
        ids = iface.activeNotifications(timeout=3)
        result = []
        for nid in ids:
            npath = kc._dev(f"notifications/{nid}")
            props = kc._iface(npath, "org.freedesktop.DBus.Properties")
            namespace = "org.kde.kdeconnect.device.notifications.notification"
            try:
                def _get_prop(name, default=None):
                    try:
                        return props.Get(namespace, name)
                    except Exception:
                        return default

                def _read_text():
                    for key in ("text", "notitext", "ticker"):
                        try:
                            value = props.Get(namespace, key)
                            if value:
                                return str(value)
                        except Exception:
                            continue
                    return ""

                raw_time = _get_prop("time", 0)
                try:
                    time_ms = int(raw_time or 0)
                except Exception:
                    time_ms = 0
                actions_supported = False
                actions_value: list = []
                try:
                    raw_actions = props.Get(namespace, "actions")
                    actions_value = list(raw_actions or [])
                    actions_supported = True
                except Exception:
                    actions_value = []
                    actions_supported = False
                result.append(
                    {
                        "id": str(nid),
                        "app": str(_get_prop("appName", "App")),
                        "title": str(_get_prop("title", "Notification")),
                        "text": _read_text(),
                        "internal_id": str(_get_prop("internalId", "") or ""),
                        "dismissable": bool(_get_prop("dismissable", True)),
                        "replyId": str(_get_prop("replyId", "") or ""),
                        "actions": actions_value,
                        "actions_supported": actions_supported,
                        "time_ms": time_ms if time_ms > 0 else 0,
                    }
                )
            except Exception as exc:
                kc.log.warning("Notification property read failed %s: %s", nid, exc)
                result.append(
                    {
                        "id": str(nid),
                        "app": "App",
                        "title": "Notification",
                        "text": "",
                        "dismissable": True,
                        "internal_id": "",
                        "replyId": "",
                        "actions": [],
                        "actions_supported": False,
                        "time_ms": 0,
                    }
                )
        return result
    except Exception as exc:
        kc.log.warning("Notifications fetch failed: %s", exc)
        return []


def dismiss_notification(kc, notif_id):
    ok = False
    try:
        npath = kc._dev(f"notifications/{notif_id}")
        kc._iface(
            npath,
            "org.kde.kdeconnect.device.notifications.notification",
        ).dismiss()
        ok = True
    except Exception as exc:
        kc.log.warning("Notification dismiss failed %s: %s", notif_id, exc)

    if not ok:
        for candidate in ("dismiss", "clear", "default"):
            try:
                kc._iface(
                    kc._dev("notifications"),
                    "org.kde.kdeconnect.device.notifications",
                ).sendAction(str(notif_id), candidate)
                ok = True
                break
            except Exception:
                continue
    return ok


def open_notification_reply(kc, notif_id):
    try:
        npath = kc._dev(f"notifications/{notif_id}")
        kc._iface(
            npath,
            "org.kde.kdeconnect.device.notifications.notification",
        ).reply()
        return True
    except Exception as exc:
        kc.log.warning("Notification quick-reply open failed %s: %s", notif_id, exc)
        return False


def reply_notification(kc, reply_id, message):
    try:
        kc._iface(
            kc._dev("notifications"),
            "org.kde.kdeconnect.device.notifications",
        ).sendReply(reply_id, message)
        return True
    except Exception as exc:
        kc.log.warning("Notification reply failed %s: %s", reply_id, exc)
        return False


def send_notification_action(kc, key, action):
    try:
        kc._iface(
            kc._dev("notifications"),
            "org.kde.kdeconnect.device.notifications",
        ).sendAction(key, action)
        return True
    except Exception as exc:
        kc.log.warning("Notification action failed %s/%s: %s", key, action, exc)
        return False


def suppress_native_notification_popups(enable: bool = True) -> bool:
    target_action = "None" if enable else "Popup"
    target_history = "false" if enable else "true"
    cfg_candidates = [
        Path.home() / ".config" / "knotifications6" / "kdeconnect.notifyrc",
        Path.home() / ".config" / "knotifications6" / "kdeconnectd.notifyrc",
        Path.home() / ".config" / "knotifications5" / "kdeconnect.notifyrc",
        Path.home() / ".config" / "knotifications5" / "kdeconnectd.notifyrc",
    ]

    event_names = {
        "notification",
        "callReceived",
        "missedCall",
        "pairingRequest",
        "batteryLow",
        "pingReceived",
        "remoteLockSuccess",
        "remoteLockFailure",
        "textShareReceived",
        "error",
    }

    def _collect_events_from(path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return
        for raw in lines:
            stripped = raw.strip()
            if not (stripped.startswith("[Event/") and stripped.endswith("]")):
                continue
            name = stripped[len("[Event/"):-1].strip()
            if name:
                event_names.add(name)

    for cfg in cfg_candidates:
        _collect_events_from(cfg)

    system_notifyrc_candidates = [
        Path("/etc/xdg/knotifications6/kdeconnect.notifyrc"),
        Path("/etc/xdg/knotifications5/kdeconnect.notifyrc"),
        Path("/usr/share/knotifications6/kdeconnect.notifyrc"),
        Path("/usr/share/knotifications5/kdeconnect.notifyrc"),
        Path.home() / ".nix-profile" / "share" / "knotifications6" / "kdeconnect.notifyrc",
        Path.home() / ".nix-profile" / "share" / "knotifications5" / "kdeconnect.notifyrc",
    ]
    kdeconnect_cli = shutil.which("kdeconnect-cli")
    if kdeconnect_cli:
        try:
            root = Path(kdeconnect_cli).resolve().parents[1]
            system_notifyrc_candidates.extend(
                [
                    root / "share" / "knotifications6" / "kdeconnect.notifyrc",
                    root / "share" / "knotifications5" / "kdeconnect.notifyrc",
                ]
            )
        except Exception:
            pass
    for cfg in system_notifyrc_candidates:
        _collect_events_from(cfg)

    ordered_events = sorted(event_names, key=lambda value: value.lower())
    changed_any = False
    for cfg_path in cfg_candidates:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.RawConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        old_text = ""
        try:
            if cfg_path.exists():
                old_text = cfg_path.read_text(encoding="utf-8")
                parser.read_string(old_text)
        except Exception:
            old_text = ""
        for event_name in ordered_events:
            section = f"Event/{event_name}"
            if not parser.has_section(section):
                parser.add_section(section)
            parser.set(section, "Action", target_action)
            parser.set(section, "ShowInHistory", target_history)
        buf = io.StringIO()
        parser.write(buf, space_around_delimiters=False)
        new_text = buf.getvalue()
        if new_text != old_text:
            cfg_path.write_text(new_text, encoding="utf-8")
            changed_any = True

    try:
        subprocess.run(
            ["systemctl", "--user", "try-restart", "kdeconnectd.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
    except Exception:
        pass
    return changed_any
