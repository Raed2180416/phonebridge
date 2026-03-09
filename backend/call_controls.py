"""Shared call controls used by call popup and Calls page."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from backend.adb_bridge import ADBBridge
from backend.state import state


@dataclass
class CallMuteResult:
    ok: bool
    route: str
    reason: str = ""


def _set_local_mic_mute(muted: bool) -> bool:
    try:
        from backend import call_audio

        if call_audio.set_input_muted(bool(muted)):
            return True
    except Exception:
        pass
    target = "1" if muted else "0"
    commands = [
        ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", target],
        ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", target],
    ]
    for cmd in commands:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _phone_route_active() -> bool:
    status = str(state.get("call_route_status", "phone") or "phone")
    if status == "pc_active":
        return False
    return not bool(state.get("call_audio_active", False))


def _route_label() -> str:
    return "phone" if _phone_route_active() else "laptop"


def set_call_muted(muted: bool) -> CallMuteResult:
    desired = bool(muted)
    route = _route_label()
    phone_ok = False
    local_ok = False

    if route == "laptop":
        local_ok = _set_local_mic_mute(desired)
        if not desired:
            # Recovery path on unmute: some OEM stacks can keep telecom muted.
            phone_ok = ADBBridge().set_call_muted(False)
    else:
        phone_ok = ADBBridge().set_call_muted(desired)
        if not desired:
            # Recovery path on unmute: local source can remain muted.
            local_ok = _set_local_mic_mute(False)

    ok = bool(local_ok if route == "laptop" else phone_ok)
    if not desired and not ok:
        ok = bool(phone_ok or local_ok)

    if ok:
        state.set("call_muted", desired)
        return CallMuteResult(ok=True, route=route)
    return CallMuteResult(ok=False, route=route, reason="mute command failed")

