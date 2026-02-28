"""Deterministic tests for call-audio settings application."""

from __future__ import annotations

import pytest

import backend.call_audio as call_audio


class _FakeAudio:
    def __init__(self):
        self.calls = []
        self._default_sink = "sink.orig"
        self._default_source = "source.orig"
        self._sink_vol = 77
        self._source_vol = 88

    def available(self):
        return True

    def default_sink(self):
        return self._default_sink

    def default_source(self):
        return self._default_source

    def get_sink_volume(self, _target):
        return self._sink_vol

    def get_source_volume(self, _target):
        return self._source_vol

    def set_default_sink(self, value):
        self.calls.append(("set_default_sink", value))
        return True

    def set_default_source(self, value):
        self.calls.append(("set_default_source", value))
        return True

    def set_sink_volume(self, target, pct):
        self.calls.append(("set_sink_volume", target, int(pct)))
        return True

    def set_source_volume(self, target, pct):
        self.calls.append(("set_source_volume", target, int(pct)))
        return True

    def set_source_mute(self, muted, target):
        self.calls.append(("set_source_mute", bool(muted), target))
        return True


@pytest.fixture(autouse=True)
def _reset_call_audio_session():
    call_audio._SESSION_ACTIVE = False
    call_audio._SESSION_SNAPSHOT = {}
    yield
    call_audio._SESSION_ACTIVE = False
    call_audio._SESSION_SNAPSHOT = {}


def test_apply_saved_settings_applies_devices_and_volumes(monkeypatch):
    fake = _FakeAudio()
    monkeypatch.setattr(call_audio, "_audio", lambda: fake)
    monkeypatch.setattr(call_audio.settings, "get", lambda k, d=None: {
        "call_output_device": "sink.a",
        "call_input_device": "source.b",
        "call_output_volume_pct": 130,
        "call_input_volume_pct": 125,
    }.get(k, d))

    call_audio.apply_saved_settings()

    assert ("set_default_sink", "sink.a") in fake.calls
    assert ("set_default_source", "source.b") in fake.calls
    assert ("set_sink_volume", "sink.a", 130) in fake.calls
    assert ("set_source_volume", "source.b", 125) in fake.calls


def test_set_input_muted_uses_selected_input_device(monkeypatch):
    fake = _FakeAudio()
    monkeypatch.setattr(call_audio, "_audio", lambda: fake)
    monkeypatch.setattr(call_audio.settings, "get", lambda k, d=None: {
        "call_input_device": "source.custom",
    }.get(k, d))

    ok = call_audio.set_input_muted(True)

    assert ok is True
    assert ("set_source_mute", True, "source.custom") in fake.calls


def test_call_session_restores_previous_system_audio(monkeypatch):
    fake = _FakeAudio()
    monkeypatch.setattr(call_audio, "_audio", lambda: fake)
    monkeypatch.setattr(call_audio.settings, "get", lambda k, d=None: {
        "call_output_device": "sink.call",
        "call_input_device": "source.call",
        "call_output_volume_pct": 130,
        "call_input_volume_pct": 125,
    }.get(k, d))

    assert call_audio.begin_session_if_needed() is True
    call_audio.apply_saved_settings()
    assert call_audio.session_active() is True
    assert ("set_default_sink", "sink.call") in fake.calls
    assert ("set_default_source", "source.call") in fake.calls

    fake.calls.clear()
    assert call_audio.end_session_restore() is True
    assert call_audio.session_active() is False
    assert ("set_default_sink", "sink.orig") in fake.calls
    assert ("set_default_source", "source.orig") in fake.calls
    assert ("set_sink_volume", "sink.orig", 77) in fake.calls
    assert ("set_source_volume", "source.orig", 88) in fake.calls
