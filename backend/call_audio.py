"""Call-audio device and level controls (laptop route)."""

from __future__ import annotations

import backend.settings_store as settings
from backend.linux_audio import LinuxAudio

_SESSION_ACTIVE = False
_SESSION_SNAPSHOT = {}


def _audio() -> LinuxAudio:
    return LinuxAudio()


def list_output_devices():
    audio = _audio()
    if not audio.available():
        return []
    return audio.list_sinks()


def list_input_devices():
    audio = _audio()
    if not audio.available():
        return []
    return audio.list_sources()


def selected_output_device() -> str:
    return str(settings.get("call_output_device", "") or "").strip()


def selected_input_device() -> str:
    return str(settings.get("call_input_device", "") or "").strip()


def set_output_device(device_name_or_id: str, *, persist: bool = True) -> bool:
    value = str(device_name_or_id or "").strip()
    audio = _audio()
    ok = True if not value else bool(audio.set_default_sink(value))
    if persist:
        settings.set("call_output_device", value)
    return ok


def set_input_device(device_name_or_id: str, *, persist: bool = True) -> bool:
    value = str(device_name_or_id or "").strip()
    audio = _audio()
    ok = True if not value else bool(audio.set_default_source(value))
    if persist:
        settings.set("call_input_device", value)
    return ok


def output_volume_pct() -> int | None:
    return _audio().get_sink_volume(selected_output_device())


def input_volume_pct() -> int | None:
    return _audio().get_source_volume(selected_input_device())


def set_output_volume_pct(value: int, *, persist: bool = True) -> bool:
    pct = max(0, min(200, int(value)))
    ok = bool(_audio().set_sink_volume(selected_output_device(), pct))
    if persist:
        settings.set("call_output_volume_pct", pct)
    return ok


def set_input_volume_pct(value: int, *, persist: bool = True) -> bool:
    pct = max(0, min(200, int(value)))
    ok = bool(_audio().set_source_volume(selected_input_device(), pct))
    if persist:
        settings.set("call_input_volume_pct", pct)
    return ok


def set_input_muted(muted: bool) -> bool:
    return bool(_audio().set_source_mute(bool(muted), selected_input_device()))


def set_output_muted(muted: bool) -> bool:
    return bool(_audio().set_sink_mute(bool(muted), selected_output_device()))


def session_active() -> bool:
    return bool(_SESSION_ACTIVE)


def begin_session_if_needed() -> bool:
    global _SESSION_ACTIVE, _SESSION_SNAPSHOT
    if _SESSION_ACTIVE:
        return True
    audio = _audio()
    if not audio.available():
        return False
    _SESSION_SNAPSHOT = {
        "default_sink": str(audio.default_sink() or ""),
        "default_source": str(audio.default_source() or ""),
        "sink_volume_pct": audio.get_sink_volume(""),
        "source_volume_pct": audio.get_source_volume(""),
    }
    _SESSION_ACTIVE = True
    return True


def end_session_restore() -> bool:
    global _SESSION_ACTIVE, _SESSION_SNAPSHOT
    if not _SESSION_ACTIVE:
        return True
    audio = _audio()
    snapshot = dict(_SESSION_SNAPSHOT or {})
    _SESSION_ACTIVE = False
    _SESSION_SNAPSHOT = {}
    if not audio.available():
        return False
    sink = str(snapshot.get("default_sink") or "").strip()
    source = str(snapshot.get("default_source") or "").strip()
    if sink:
        audio.set_default_sink(sink)
    if source:
        audio.set_default_source(source)
    sink_vol = snapshot.get("sink_volume_pct")
    source_vol = snapshot.get("source_volume_pct")
    if isinstance(sink_vol, int) and sink_vol >= 0:
        audio.set_sink_volume(sink, sink_vol)
    if isinstance(source_vol, int) and source_vol >= 0:
        audio.set_source_volume(source, source_vol)
    return True


def apply_saved_settings() -> None:
    begin_session_if_needed()
    audio = _audio()
    if not audio.available():
        return
    out_dev = selected_output_device()
    in_dev = selected_input_device()
    if out_dev:
        audio.set_default_sink(out_dev)
    if in_dev:
        audio.set_default_source(in_dev)
    out_vol = int(settings.get("call_output_volume_pct", -1) or -1)
    in_vol = int(settings.get("call_input_volume_pct", -1) or -1)
    if out_vol >= 0:
        audio.set_sink_volume(out_dev, out_vol)
    if in_vol >= 0:
        audio.set_source_volume(in_dev, in_vol)
