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


def test_get_call_state_fast_fails_over_from_bad_target(monkeypatch):
    monkeypatch.setattr("backend.adb_bridge.runtime_config.adb_target", lambda default="": "")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.phone_tailscale_ip", lambda default="": "")
    bridge = ADBBridge()
    bridge._fast_call_state_value = "unknown"
    bridge._fast_call_state_at = 0.0
    bridge._fast_call_state_fallback_at = 0.0
    monkeypatch.setattr(
        bridge,
        "_get_devices",
        lambda force=False: [
            {"serial": "USB123", "state": "device", "transport": "usb", "tail": "", "fields": {}},
            {"serial": "100.64.0.10:5555", "state": "device", "transport": "wireless", "tail": "", "fields": {}},
        ],
    )
    monkeypatch.setattr(bridge, "_ensure_wireless_keepalive_from_usb", lambda *_a, **_kw: None)

    def fake_run_adb(*args, **kwargs):
        if args[:2] == ("-s", "USB123"):
            return False, "adb: device 'USB123' not found"
        if args[:4] == ("-s", "100.64.0.10:5555", "get-state"):
            return True, "device\n"
        if args[:2] == ("-s", "100.64.0.10:5555"):
            return True, "ringing\n"
        raise AssertionError(f"unexpected adb call: {args}")

    monkeypatch.setattr(bridge, "_run_adb", fake_run_adb)
    assert bridge.get_call_state_fast() == "ringing"
