"""Telephony and contacts helpers for the ADB bridge facade."""

from __future__ import annotations

import re
import time


def answer_call(bridge) -> bool:
    ok, _ = bridge._run("shell", "input", "keyevent", "KEYCODE_HEADSETHOOK", timeout=4)
    return ok


def end_call(bridge) -> bool:
    ok, _ = bridge._run("shell", "input", "keyevent", "KEYCODE_ENDCALL", timeout=4)
    return ok


def get_call_state_fast(bridge) -> str:
    """Quick call-state check for frequent polling."""
    bridge_log = getattr(bridge, "log", None)
    serial = bridge._resolve_target(allow_connect=False)
    if not serial:
        if bridge_log is not None:
            bridge_log.debug("get_call_state_fast: no serial (allow_connect=False)")
        return "unknown"

    now = time.monotonic()
    cached_value = str(getattr(bridge, "_fast_call_state_value", "unknown") or "unknown").strip().lower()
    cached_at = float(getattr(bridge, "_fast_call_state_at", 0.0) or 0.0)
    fallback_at = float(getattr(bridge, "_fast_call_state_fallback_at", 0.0) or 0.0)

    serial, ok, out = bridge._run_on_serial(
        serial,
        "shell",
        "getprop",
        "gsm.call.state",
        timeout=1,
        allow_connect_retry=True,
    )
    if ok:
        prop = (out or "").strip().lower()
        if prop == "ringing":
            bridge._fast_call_state_value = "ringing"
            bridge._fast_call_state_at = now
            return "ringing"
        if prop in ("offhook", "active"):
            bridge._fast_call_state_value = "offhook"
            bridge._fast_call_state_at = now
            return "offhook"
        if prop == "idle":
            bridge._fast_call_state_value = "idle"
            bridge._fast_call_state_at = now
            return "idle"

    # `dumpsys telephony.registry` is materially heavier than getprop and is
    # the command that repeatedly times out over wireless ADB. Rate-limit it so
    # fallback polling cannot stall the whole app during idle/background use.
    if (now - fallback_at) < 4.0:
        if cached_value in {"ringing", "offhook", "idle"} and (now - cached_at) < 2.5:
            return cached_value
        if bridge_log is not None:
            bridge_log.debug(
                "get_call_state_fast: skipping dumpsys fallback rate_limit serial=%s cached=%s age=%.2fs",
                serial,
                cached_value,
                max(0.0, now - cached_at),
            )
        return "unknown"

    bridge._fast_call_state_fallback_at = now
    serial, ok2, out2 = bridge._run_on_serial(
        serial,
        "shell",
        "dumpsys",
        "telephony.registry",
        timeout=2,
        allow_connect_retry=True,
    )
    if ok2:
        values = []
        try:
            for raw in (out2 or "").splitlines():
                match = re.search(r"\bmCallState\s*=\s*(-?\d+)", raw.strip())
                if match:
                    values.append(int(match.group(1)))
        except Exception:
            values = []
        if values:
            if any(v == 2 for v in values):
                bridge._fast_call_state_value = "offhook"
                bridge._fast_call_state_at = now
                return "offhook"
            if any(v == 1 for v in values):
                bridge._fast_call_state_value = "ringing"
                bridge._fast_call_state_at = now
                return "ringing"
            if all(v == 0 for v in values):
                bridge._fast_call_state_value = "idle"
                bridge._fast_call_state_at = now
                return "idle"
    if bridge_log is not None:
        bridge_log.debug("get_call_state_fast: returning unknown (serial=%s ok=%s ok2=%s)", serial, ok, ok2)
    return "unknown"


def get_call_state(bridge) -> str:
    """Return telephony call state: idle | ringing | offhook | unknown."""
    serial = bridge._resolve_target(allow_connect=True)
    if not serial:
        return "unknown"
    serial, ok, out = bridge._run_on_serial(
        serial,
        "shell",
        "dumpsys",
        "telephony.registry",
        timeout=5,
        allow_connect_retry=True,
    )
    if ok:
        values = []
        try:
            for raw in (out or "").splitlines():
                line = raw.strip()
                match = re.search(r"\bmCallState\s*=\s*(-?\d+)", line)
                if match:
                    values.append(int(match.group(1)))
        except Exception:
            values = []
        if values:
            if any(v == 2 for v in values):
                return "offhook"
            if any(v == 1 for v in values):
                return "ringing"
            if all(v == 0 for v in values):
                return "idle"

    _serial, ok2, out2 = bridge._run_on_serial(
        serial,
        "shell",
        "dumpsys",
        "telecom",
        timeout=5,
        allow_connect_retry=True,
    )
    if not ok2:
        return "unknown"
    text = str(out2 or "").lower()
    if not text.strip():
        return "unknown"
    if re.search(r"\bstate\s*=\s*ringing\b", text) or re.search(r"\bringing\b", text):
        return "ringing"
    if re.search(r"\bstate\s*=\s*(active|dialing|connecting|holding)\b", text):
        return "offhook"
    if any(k in text for k in (" offhook", "in call", "incall")):
        return "offhook"
    if re.search(r"\bstate\s*=\s*idle\b", text):
        return "idle"
    return "unknown"


def phone_call_active(bridge):
    status = get_call_state(bridge)
    if status == "unknown":
        return None
    return status in {"ringing", "offhook"}


def set_call_muted(bridge, muted: bool) -> bool:
    if muted:
        active = phone_call_active(bridge)
        if active is False:
            return False
    telecom_flag = "true" if muted else "false"
    if muted:
        attempts = [
            ("shell", "cmd", "telecom", "mute", telecom_flag),
            ("shell", "cmd", "telecom", "set-mute", telecom_flag),
            ("shell", "cmd", "audio", "adj-mute", "0"),
        ]
    else:
        attempts = [
            ("shell", "cmd", "telecom", "mute", telecom_flag),
            ("shell", "cmd", "telecom", "set-mute", telecom_flag),
            ("shell", "cmd", "audio", "adj-unmute", "0"),
        ]
    for args in attempts:
        ok, _ = bridge._run(*args, timeout=4)
        if ok:
            return True
    return False


def get_contacts(bridge, limit: int = 300):
    ok, out = bridge._run(
        "shell", "content", "query",
        "--uri", "content://contacts/phones",
        "--projection", "display_name:number",
        timeout=10,
    )
    if not ok:
        return []
    contacts = []
    for line in out.splitlines():
        match = re.search(r"display_name=(.*?), number=(.*)$", line)
        if not match:
            continue
        name = (match.group(1) or "").strip()
        number = (match.group(2) or "").strip()
        if number:
            contacts.append({"name": name or number, "phone": number})
        if len(contacts) >= limit:
            break
    return contacts


def get_recent_calls(bridge, limit: int = 30):
    ok, out = bridge._run(
        "shell", "content", "query",
        "--uri", "content://call_log/calls",
        "--projection", "number:name:type:date",
        timeout=10,
    )
    if not ok:
        return []
    rows = []
    for line in out.splitlines():
        match = re.search(r"number=(.*?), name=(.*?), type=(\d+), date=(\d+)", line)
        if not match:
            continue
        number = (match.group(1) or "").strip()
        name = (match.group(2) or "").strip()
        type_code = int(match.group(3))
        date_ms = int(match.group(4))
        if type_code == 1:
            event = "incoming"
        elif type_code == 2:
            event = "outgoing"
        elif type_code == 3:
            event = "missed"
        elif type_code == 6:
            event = "rejected"
        else:
            event = "other"
        rows.append(
            {
                "number": number,
                "name": name or number,
                "event": event,
                "date_ms": date_ms,
            }
        )
    rows.sort(key=lambda row: row["date_ms"], reverse=True)
    return rows[:limit]
