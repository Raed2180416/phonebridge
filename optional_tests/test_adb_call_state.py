"""Deterministic tests for ADBBridge call state parsing."""

from __future__ import annotations

from backend.adb_bridge import ADBBridge


def _mk_bridge(monkeypatch, *, ok=True, out="", serial="SERIAL"):
    bridge = ADBBridge()
    monkeypatch.setattr(bridge, "_resolve_target", lambda allow_connect=True: serial)
    monkeypatch.setattr(bridge, "_run_adb", lambda *args, **kwargs: (ok, out))
    return bridge


def test_get_call_state_idle(monkeypatch):
    bridge = _mk_bridge(monkeypatch, out="mCallState=0\n")
    assert bridge.get_call_state() == "idle"


def test_get_call_state_ringing(monkeypatch):
    bridge = _mk_bridge(monkeypatch, out="x\nmCallState = 1\n")
    assert bridge.get_call_state() == "ringing"


def test_get_call_state_offhook_precedence(monkeypatch):
    bridge = _mk_bridge(monkeypatch, out="mCallState=1\nmCallState=2\nmCallState=0\n")
    assert bridge.get_call_state() == "offhook"


def test_get_call_state_unknown_when_unavailable(monkeypatch):
    bridge = _mk_bridge(monkeypatch, ok=False, out="")
    assert bridge.get_call_state() == "unknown"

    bridge2 = _mk_bridge(monkeypatch, ok=True, out="", serial=None)
    assert bridge2.get_call_state() == "unknown"


def test_phone_call_active_compatibility_wrapper(monkeypatch):
    bridge = _mk_bridge(monkeypatch, out="mCallState=2\n")
    assert bridge._phone_call_active() is True

    bridge = _mk_bridge(monkeypatch, out="mCallState=1\n")
    assert bridge._phone_call_active() is True

    bridge = _mk_bridge(monkeypatch, out="mCallState=0\n")
    assert bridge._phone_call_active() is False

    bridge = _mk_bridge(monkeypatch, ok=False, out="")
    assert bridge._phone_call_active() is None
