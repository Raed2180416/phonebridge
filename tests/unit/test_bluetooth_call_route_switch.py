"""Deterministic tests for Bluetooth call route release behavior."""

from __future__ import annotations

from backend.bluetooth_manager import BluetoothManager


def test_release_call_audio_route_disconnects_call_profiles(monkeypatch):
    mgr = BluetoothManager()
    monkeypatch.setattr(mgr, "connected_phone_macs", lambda preferred_names=None: ["AA:BB:CC:DD:EE:FF"])
    monkeypatch.setattr(mgr, "disconnect_call_profiles", lambda mac: (True, "ok"))
    monkeypatch.setattr(mgr, "disconnect", lambda mac: (_ for _ in ()).throw(AssertionError("disconnect fallback not expected")))

    changed, msg = mgr.release_call_audio_route(["phone"], force_disconnect=True)
    assert changed is True
    assert "Released" in msg


def test_release_call_audio_route_falls_back_to_disconnect(monkeypatch):
    mgr = BluetoothManager()
    monkeypatch.setattr(mgr, "connected_phone_macs", lambda preferred_names=None: ["AA:BB:CC:DD:EE:FF"])
    monkeypatch.setattr(mgr, "disconnect_call_profiles", lambda mac: (False, "none"))
    monkeypatch.setattr(mgr, "disconnect", lambda mac: (True, "ok"))

    changed, _ = mgr.release_call_audio_route(["phone"], force_disconnect=True)
    assert changed is True


def test_release_call_audio_route_no_candidates(monkeypatch):
    mgr = BluetoothManager()
    monkeypatch.setattr(mgr, "connected_phone_macs", lambda preferred_names=None: [])

    changed, msg = mgr.release_call_audio_route(["phone"], force_disconnect=True)
    assert changed is False
    assert "No connected phone candidates" in msg
