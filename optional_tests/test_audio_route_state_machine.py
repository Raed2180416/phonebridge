"""Deterministic tests for call/audio route state transitions.

These tests are intentionally isolated in `optional_tests/` so they are easy
to remove without touching runtime modules.
"""

from __future__ import annotations

import pytest

import backend.audio_route as audio_route


@pytest.fixture(autouse=True)
def _reset_audio_route(monkeypatch):
    store = {}
    monkeypatch.setattr(audio_route.settings, "set", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(audio_route.settings, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(audio_route.state, "set", lambda key, value: None)

    audio_route._sources.clear()
    audio_route._sources.update(
        {
            "ui_global_toggle": False,
            "call_pc_active": False,
        }
    )
    audio_route._audio_proc = None
    audio_route._audio_mode = None
    yield
    audio_route._audio_proc = None
    audio_route._audio_mode = None


def test_desired_mode_prioritizes_call_route_over_global_media():
    audio_route.set_source("ui_global_toggle", True)
    audio_route.set_source("call_pc_active", True)

    assert audio_route._desired_mode() == "audio"
    assert audio_route._desired_mode(suspend_ui_global=True) == "audio"

    audio_route.set_source("call_pc_active", False)
    assert audio_route._desired_mode() == "audio_output"
    assert audio_route._desired_mode(suspend_ui_global=True) is None


def test_sync_transitions_between_media_call_and_idle(monkeypatch):
    events = []
    runtime = {"running": False}

    def fake_is_running():
        return runtime["running"]

    def fake_start(mode, adb=None):
        events.append(("start", mode))
        runtime["running"] = True
        audio_route._audio_mode = mode
        return True

    def fake_stop():
        events.append(("stop", audio_route._audio_mode))
        runtime["running"] = False
        audio_route._audio_mode = None
        return True

    monkeypatch.setattr(audio_route, "_is_running", fake_is_running)
    monkeypatch.setattr(audio_route, "_start_proc", fake_start)
    monkeypatch.setattr(audio_route, "_stop_proc", fake_stop)
    monkeypatch.setattr(audio_route, "_bt_call_profile_present", lambda: True)
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", lambda: True)

    audio_route.set_source("ui_global_toggle", True)
    assert audio_route.sync() is True
    assert events == [("start", "audio_output")]

    audio_route.set_source("call_pc_active", True)
    assert audio_route.sync() is True
    assert events[-2:] == [("stop", "audio_output"), ("start", "audio")]

    audio_route.set_source("call_pc_active", False)
    assert audio_route.sync(suspend_ui_global=True) is True
    assert events[-1] == ("stop", "audio")


def test_sync_is_noop_when_mode_is_already_correct(monkeypatch):
    events = []
    runtime = {"running": True}
    audio_route._audio_mode = "audio_output"
    audio_route.set_source("ui_global_toggle", True)

    monkeypatch.setattr(audio_route, "_is_running", lambda: runtime["running"])
    monkeypatch.setattr(audio_route, "_start_proc", lambda mode, adb=None: events.append(("start", mode)) or True)
    monkeypatch.setattr(
        audio_route, "_stop_proc", lambda: events.append(("stop", audio_route._audio_mode)) or True
    )

    assert audio_route.sync() is True
    assert events == []


def test_call_mode_refuses_when_bluetooth_call_profile_missing(monkeypatch):
    monkeypatch.setattr(audio_route, "_cleanup_orphan_audio_procs", lambda exclude=None: 0)
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", lambda: False)
    monkeypatch.setattr(audio_route.ADBBridge, "launch_scrcpy", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))

    ok = audio_route._start_proc("audio")
    assert ok is False
    assert audio_route._audio_proc is None
    assert audio_route._audio_mode is None


def test_call_mode_uses_external_bt_backend_when_profile_is_present(monkeypatch):
    monkeypatch.setattr(audio_route, "_cleanup_orphan_audio_procs", lambda exclude=None: 0)
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", lambda: True)

    ok = audio_route._start_proc("audio")
    assert ok is True
    assert audio_route._audio_mode == "audio"
    assert audio_route.active_backend() == "external_bt"


def test_sync_restores_call_audio_session_when_call_route_ends(monkeypatch):
    runtime = {"running": True}
    restored = {"count": 0}
    audio_route._audio_mode = "audio"
    audio_route.set_source("call_pc_active", False)

    monkeypatch.setattr(audio_route, "_is_running", lambda: runtime["running"])
    monkeypatch.setattr(audio_route, "_stop_proc", lambda: runtime.__setitem__("running", False) or True)
    monkeypatch.setattr(audio_route, "_restore_call_audio_session_if_needed", lambda: restored.__setitem__("count", restored["count"] + 1))

    res = audio_route.sync_result(suspend_ui_global=True, call_retry_ms=0)

    assert res.ok is True
    assert res.status in {"stopped", "noop"}
    assert restored["count"] >= 1
