"""Notification normalization and short-lived dismissal anti-race state."""
from __future__ import annotations

import os
import time
from typing import Any


# Kept for compatibility with older tests/tooling that may still reference these.
_STATE_PATH = os.path.expanduser("~/.config/phonebridge/notifications_state.json")
_CACHE: dict[str, Any] | None = None
_SESSION_DISMISS_TTL_MS = 2_500
_SESSION_CALL_TTL_MS = 15_000
_SESSION_HIDDEN_UNTIL_MS_BY_ID: dict[str, int] = {}
_SESSION_HIDDEN_UNTIL_MS_BY_CALL_KEY: dict[str, int] = {}


def _prune_session_hidden(now_ms: int) -> None:
    stale = [nid for nid, until_ms in _SESSION_HIDDEN_UNTIL_MS_BY_ID.items() if int(until_ms) <= now_ms]
    for nid in stale:
        _SESSION_HIDDEN_UNTIL_MS_BY_ID.pop(nid, None)
    stale_call_keys = [key for key, until_ms in _SESSION_HIDDEN_UNTIL_MS_BY_CALL_KEY.items() if int(until_ms) <= now_ms]
    for call_key in stale_call_keys:
        _SESSION_HIDDEN_UNTIL_MS_BY_CALL_KEY.pop(call_key, None)


def _hide_in_session(notification_id: str, *, ttl_ms: int = _SESSION_DISMISS_TTL_MS) -> None:
    nid = str(notification_id or "").strip()
    if not nid:
        return
    now_ms = int(time.time() * 1000)
    _SESSION_HIDDEN_UNTIL_MS_BY_ID[nid] = now_ms + max(0, int(ttl_ms))


def _hide_call_key_in_session(call_key: str, *, ttl_ms: int = _SESSION_CALL_TTL_MS) -> None:
    key = str(call_key or "").strip()
    if not key:
        return
    now_ms = int(time.time() * 1000)
    _SESSION_HIDDEN_UNTIL_MS_BY_CALL_KEY[key] = now_ms + max(0, int(ttl_ms))


def _normalize_key_part(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _phone_notification_family(row: dict[str, Any]) -> bool:
    app = _normalize_key_part(row.get("app"))
    internal_id = _normalize_key_part(row.get("internal_id") or row.get("internalId"))
    package_tokens = (
        "com.android",
        "com.google.android.dialer",
        "com.samsung",
        "com.oneplus",
        "com.xiaomi",
        "com.huawei",
    )
    if any(token in internal_id for token in package_tokens):
        return True
    if any(token in app for token in package_tokens):
        return True
    app_tokens = {token for token in app.replace(".", " ").replace("/", " ").replace("_", " ").replace("-", " ").split() if token}
    return bool({"phone", "dialer", "telecom", "incallui"} & app_tokens)


def phone_call_notification_key(row: dict[str, Any] | None) -> str:
    payload = dict(row or {})
    if not _phone_notification_family(payload):
        return ""
    internal_id = _normalize_key_part(payload.get("internal_id") or payload.get("internalId"))
    if internal_id:
        return f"phone:internal:{internal_id}"
    app = _normalize_key_part(payload.get("app"))
    title = _normalize_key_part(payload.get("title"))
    text = _normalize_key_part(payload.get("text"))
    actions = "|".join(sorted(_normalize_key_part(item) for item in list(payload.get("actions") or []) if _normalize_key_part(item)))
    if not title and not text and not actions:
        return ""
    return f"phone:content:{app}|{title}|{text}|{actions}"


def _is_kdeconnect_meta_notification(row: dict[str, Any]) -> bool:
    app = str(row.get("app") or "").strip().lower()
    title = str(row.get("title") or "").strip().lower()
    text = str(row.get("text") or "").strip().lower()
    internal_id = str(row.get("internal_id") or row.get("internalId") or "").strip().lower()
    actions = list(row.get("actions") or [])
    has_reply = bool(str(row.get("replyId") or "").strip())
    if has_reply or actions:
        return False

    app_is_kde = app in {"kde connect", "kdeconnect", "org.kde.kdeconnect"}
    internal_is_kde = "org.kde.kdeconnect" in internal_id
    if not app_is_kde and not internal_is_kde:
        return False

    blob = f"{title} {text}".strip()
    signatures = (
        "pairing request",
        "paired",
        "connected",
        "disconnected",
        "battery low",
        "ping received",
        "notification received",
        "remote lock",
        "text share received",
        "kde connect",
    )
    if title in {"kde connect", "kdeconnect", "generic notification"}:
        return True
    return any(sig in blob for sig in signatures)


def normalize_notifications(raw_notifications: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    _prune_session_hidden(now_ms)

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(list(raw_notifications or [])):
        notif = dict(item or {})
        nid = str(notif.get("id") or "").strip()
        if not nid:
            continue
        if _is_kdeconnect_meta_notification(notif):
            continue
        if int(_SESSION_HIDDEN_UNTIL_MS_BY_ID.get(nid, 0) or 0) > now_ms:
            continue
        call_key = phone_call_notification_key(notif)
        if call_key and int(_SESSION_HIDDEN_UNTIL_MS_BY_CALL_KEY.get(call_key, 0) or 0) > now_ms:
            continue

        raw_time = notif.get("time_ms")
        try:
            time_ms = int(raw_time) if raw_time is not None else 0
        except Exception:
            time_ms = 0
        if time_ms < 0:
            time_ms = 0

        actions = list(notif.get("actions") or [])
        actions_supported = bool(notif.get("actions_supported", bool(actions)))
        normalized.append(
            {
                "id": nid,
                "app": str(notif.get("app") or "App"),
                "title": str(notif.get("title") or "Notification"),
                "text": str(notif.get("text") or ""),
                "dismissable": bool(notif.get("dismissable", True)),
                "replyId": str(notif.get("replyId") or ""),
                "actions": actions,
                "actions_supported": actions_supported,
                "internal_id": str(notif.get("internal_id") or notif.get("internalId") or ""),
                "time_ms": time_ms,
                "_call_key": call_key,
                "_input_order": idx,
            }
        )

    deduped_phone_rows: dict[str, dict[str, Any]] = {}
    deduped_other_rows: list[dict[str, Any]] = []
    for row in normalized:
        call_key = str(row.get("_call_key") or "").strip()
        if not call_key:
            deduped_other_rows.append(row)
            continue
        existing = deduped_phone_rows.get(call_key)
        if existing is None:
            deduped_phone_rows[call_key] = row
            continue
        existing_time = int(existing.get("time_ms") or 0)
        row_time = int(row.get("time_ms") or 0)
        if row_time > existing_time:
            deduped_phone_rows[call_key] = row
            continue
        if row_time == existing_time and int(row.get("_input_order") or 0) > int(existing.get("_input_order") or 0):
            deduped_phone_rows[call_key] = row
    normalized = deduped_other_rows + list(deduped_phone_rows.values())

    timed = [r for r in normalized if int(r.get("time_ms") or 0) > 0]
    if not timed:
        ordered = normalized
    else:
        timed.sort(key=lambda x: (-int(x.get("time_ms") or 0), int(x.get("_input_order") or 0)))
        untimed = [r for r in normalized if int(r.get("time_ms") or 0) <= 0]
        ordered = timed + untimed

    for row in ordered:
        row.pop("_call_key", None)
        row.pop("_input_order", None)
    return ordered


def record_dismissed(notification_id: str, payload: dict[str, Any] | None = None) -> None:
    _ = payload
    _hide_in_session(notification_id)


def record_dismissed_many(notification_ids: list[str]) -> None:
    for notification_id in list(notification_ids or []):
        _hide_in_session(str(notification_id or "").strip())


def record_hidden_call_keys(call_keys: list[str], *, ttl_ms: int = _SESSION_CALL_TTL_MS) -> None:
    for call_key in list(call_keys or []):
        _hide_call_key_in_session(str(call_key or "").strip(), ttl_ms=ttl_ms)
