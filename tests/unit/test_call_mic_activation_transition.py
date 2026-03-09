"""Deterministic call-mic activation transition tests.

This file exercises the pending-to-active call-mic transition retry logic.
"""

from __future__ import annotations

import pytest

import backend.audio_route as audio_route


@pytest.fixture(autouse=True)
def _reset_audio_route(monkeypatch):
    store = {
        "audio_redirect": False,
        "call_route_status": "phone",
        "call_route_reason": "",
        "call_route_backend": "none",
        "call_audio_active": False,
    }

    monkeypatch.setattr(audio_route.settings, "set", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(audio_route.settings, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(audio_route.state, "set", lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(audio_route.state, "set_many", lambda values: store.update(dict(values or {})))
    monkeypatch.setattr(audio_route.state, "get", lambda key, default=None: store.get(key, default))

    audio_route._sources.clear()
    audio_route._sources.update({"ui_global_toggle": False, "call_pc_active": False})
    audio_route._audio_proc = None
    audio_route._audio_mode = None
    yield store
    audio_route._audio_proc = None
    audio_route._audio_mode = None


def test_call_route_transitions_pending_to_active_within_retry(monkeypatch, _reset_audio_route):
    runtime = {"running": False, "mic_checks": 0}

    monkeypatch.setattr(audio_route, "_cleanup_orphan_audio_procs", lambda exclude=None: 0)
    monkeypatch.setattr(audio_route, "_stop_proc", lambda: runtime.__setitem__("running", False) or True)
    monkeypatch.setattr(audio_route, "_is_running", lambda: runtime["running"])
    monkeypatch.setattr(audio_route, "active_backend", lambda: "external_bt" if runtime["running"] else "none")
    monkeypatch.setattr(audio_route, "_bt_call_profile_present", lambda: True)

    def _mic_active():
        runtime["mic_checks"] += 1
        return runtime["mic_checks"] >= 3

    def _start_proc(mode, adb=None):
        assert mode == "audio"
        runtime["running"] = True
        audio_route._audio_mode = mode
        return True

    # Keep retry loop deterministic and fast.
    tick = {"t": 0.0}
    monkeypatch.setattr(audio_route.time, "sleep", lambda _s: None)
    monkeypatch.setattr(audio_route.time, "time", lambda: (tick.__setitem__("t", tick["t"] + 0.11) or tick["t"]))
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", _mic_active)
    monkeypatch.setattr(audio_route, "_start_proc", _start_proc)

    audio_route.set_source("call_pc_active", True)
    res = audio_route.sync_result(call_retry_ms=900, retry_step_ms=20)

    assert res.ok is True
    assert res.status == "active"
    assert res.backend == "external_bt"
    assert _reset_audio_route["call_route_status"] == "pc_active"
    assert _reset_audio_route["call_audio_active"] is True


def test_call_route_fails_after_retry_timeout_when_mic_never_appears(monkeypatch, _reset_audio_route):
    runtime = {"running": False}
    monkeypatch.setattr(audio_route, "_cleanup_orphan_audio_procs", lambda exclude=None: 0)
    monkeypatch.setattr(audio_route, "_stop_proc", lambda: runtime.__setitem__("running", False) or True)
    monkeypatch.setattr(audio_route, "_is_running", lambda: runtime["running"])
    monkeypatch.setattr(audio_route, "active_backend", lambda: "none")
    monkeypatch.setattr(audio_route, "_bt_call_profile_present", lambda: True)
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", lambda: False)
    monkeypatch.setattr(audio_route, "_start_proc", lambda mode, adb=None: (_ for _ in ()).throw(AssertionError()))

    tick = {"t": 0.0}
    monkeypatch.setattr(audio_route.time, "sleep", lambda _s: None)
    monkeypatch.setattr(audio_route.time, "time", lambda: (tick.__setitem__("t", tick["t"] + 0.21) or tick["t"]))

    audio_route.set_source("call_pc_active", True)
    res = audio_route.sync_result(call_retry_ms=600, retry_step_ms=20)

    assert res.ok is False
    assert res.status == "failed"
    assert "mic path" in res.reason.lower()
    assert _reset_audio_route["call_route_status"] == "pc_failed"
    assert _reset_audio_route["call_audio_active"] is False


def test_call_route_never_reports_pc_active_without_mic_path(monkeypatch, _reset_audio_route):
    monkeypatch.setattr(audio_route, "_cleanup_orphan_audio_procs", lambda exclude=None: 0)
    monkeypatch.setattr(audio_route, "_is_running", lambda: False)
    monkeypatch.setattr(audio_route, "_bt_call_profile_present", lambda: True)
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", lambda: False)
    monkeypatch.setattr(audio_route, "_start_proc", lambda mode, adb=None: False)

    audio_route.set_source("call_pc_active", True)
    res = audio_route.sync_result(call_retry_ms=0)

    assert res.ok is False
    assert res.status == "pending"
    assert _reset_audio_route["call_route_status"] == "pending_pc"
    assert _reset_audio_route["call_audio_active"] is False


def test_switch_to_phone_with_suspend_ui_global_keeps_phone_route(monkeypatch, _reset_audio_route):
    runtime = {"running": True}
    audio_route._audio_mode = "audio"

    monkeypatch.setattr(audio_route, "_cleanup_orphan_audio_procs", lambda exclude=None: 0)
    monkeypatch.setattr(audio_route, "_is_running", lambda: runtime["running"])
    monkeypatch.setattr(audio_route, "_stop_proc", lambda: runtime.__setitem__("running", False) or True)
    monkeypatch.setattr(audio_route, "_bt_call_profile_present", lambda: True)
    monkeypatch.setattr(audio_route, "_bt_call_mic_path_active", lambda: True)
    monkeypatch.setattr(audio_route, "active_backend", lambda: "external_bt" if runtime["running"] else "none")

    audio_route.set_source("ui_global_toggle", True)
    audio_route.set_source("call_pc_active", True)
    active = audio_route.sync_result(suspend_ui_global=True, call_retry_ms=0)
    assert active.ok is True
    assert active.status == "active"

    audio_route.set_source("call_pc_active", False)
    switched = audio_route.sync_result(suspend_ui_global=True, call_retry_ms=0)
    assert switched.ok is True
    assert switched.status in {"stopped", "noop"}
    assert _reset_audio_route["call_route_status"] == "phone"
    assert _reset_audio_route["call_audio_active"] is False
