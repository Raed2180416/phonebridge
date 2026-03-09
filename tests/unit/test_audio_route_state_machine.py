"""Deterministic tests for call/audio route state transitions.

These tests exercise the route-state machine without requiring live hardware.
"""

from __future__ import annotations

from types import SimpleNamespace
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
    monkeypatch.setattr(audio_route, "_bt_call_profile_present", lambda: False)
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


def test_bt_call_mic_path_active_ignores_generic_bluez_device_without_source(monkeypatch):
    def _fake_run(args, capture_output=True, text=True, timeout=0.5):
        if args[:4] == ["pactl", "list", "short", "sources"]:
            return SimpleNamespace(returncode=0, stdout="65\talsa_input.foo\n", stderr="")
        if args[:3] == ["pactl", "list", "sources"]:
            return SimpleNamespace(returncode=0, stdout="Source #65\ndevice.api = \"alsa\"\n", stderr="")
        if args[:2] == ["wpctl", "status"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Audio\n"
                    " ├─ Devices:\n"
                    " │    128. Nothing Phone (3a) Pro [bluez5]\n"
                    " ├─ Sinks:\n"
                    " │  * 64. Ryzen HD Audio Controller Speaker [vol: 0.32]\n"
                    " ├─ Sources:\n"
                    " │  * 66. Ryzen HD Audio Controller Digital Microphone [vol: 1.40]\n"
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(audio_route.subprocess, "run", _fake_run)
    assert audio_route._bt_call_mic_path_active() is False


def test_bt_call_mic_path_active_detects_bluez_source_in_wpctl_sources(monkeypatch):
    def _fake_run(args, capture_output=True, text=True, timeout=0.5):
        if args[:4] == ["pactl", "list", "short", "sources"]:
            return SimpleNamespace(returncode=0, stdout="65\talsa_input.foo\n", stderr="")
        if args[:3] == ["pactl", "list", "sources"]:
            return SimpleNamespace(returncode=0, stdout="Source #65\ndevice.api = \"alsa\"\n", stderr="")
        if args[:2] == ["wpctl", "status"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Audio\n"
                    " ├─ Devices:\n"
                    " │    128. Nothing Phone (3a) Pro [bluez5]\n"
                    " ├─ Sources:\n"
                    " │  * 201. bluez_input.12_34_56_78_90_AB.0 Handsfree [vol: 1.00]\n"
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(audio_route.subprocess, "run", _fake_run)
    assert audio_route._bt_call_mic_path_active() is True


def test_bt_call_profile_present_falls_back_to_pactl_active_profile(monkeypatch):
    def _fake_run(args, capture_output=True, text=True, timeout=0.5):
        if args[:2] == ["wpctl", "status"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Audio\n ├─ Devices:\n │    128. Nothing Phone (3a) Pro [bluez5]\n",
                stderr="",
            )
        if args[:2] == ["wpctl", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout='bluez5.profile = "off"\n',
                stderr="",
            )
        if args[:3] == ["pactl", "list", "cards"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Card #1\n"
                    "Name: bluez_card.3C_B0_ED_92_B6_90\n"
                    "Active Profile: audio-gateway\n"
                ),
                stderr="",
            )
        raise AssertionError(args)

    monkeypatch.setattr(audio_route.subprocess, "run", _fake_run)
    assert audio_route._bt_call_profile_present() is True
