"""Global phone-audio routing controller (scrcpy --no-video audio session)."""
from __future__ import annotations

import subprocess

from backend.adb_bridge import ADBBridge
import backend.settings_store as settings

_audio_proc = None


def _is_running() -> bool:
    global _audio_proc
    if _audio_proc is None:
        return False
    if _audio_proc.poll() is None:
        return True
    _audio_proc = None
    return False


def is_running() -> bool:
    return _is_running()


def start(adb: ADBBridge | None = None) -> bool:
    global _audio_proc
    if _is_running():
        return True
    bridge = adb or ADBBridge()
    proc = bridge.launch_scrcpy("audio_output")
    if not proc:
        _audio_proc = None
        return False
    _audio_proc = proc
    return True


def stop() -> bool:
    global _audio_proc
    if _audio_proc is None:
        return True
    try:
        _audio_proc.terminate()
        _audio_proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            _audio_proc.kill()
        except Exception:
            pass
    except Exception:
        pass
    _audio_proc = None
    return True


def set_enabled(enabled: bool, adb: ADBBridge | None = None) -> bool:
    want = bool(enabled)
    if want:
        ok = start(adb=adb)
        settings.set("audio_redirect", bool(ok))
        return ok
    stop()
    settings.set("audio_redirect", False)
    return True

