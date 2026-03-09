from __future__ import annotations

import pytest

from backend.adb_bridge import ADBBridge


@pytest.fixture(autouse=True)
def _reset_target_caches(monkeypatch):
    monkeypatch.setattr("backend.adb_bridge._BAD_TARGETS", {})
    monkeypatch.setattr("backend.adb_bridge._GOOD_TARGETS", {})


def test_parse_adb_devices_extracts_fields():
    devices = ADBBridge._parse_adb_devices(
        "List of devices attached\n"
        "USB123 device product:akita model:Pixel_8 device:akita transport_id:1\n"
    )
    assert devices == [
        {
            "serial": "USB123",
            "state": "device",
            "transport": "usb",
            "tail": "product:akita model:Pixel_8 device:akita transport_id:1",
            "fields": {
                "product": "akita",
                "model": "Pixel_8",
                "device": "akita",
                "transport_id": "1",
            },
        }
    ]


def test_pick_connected_target_prefers_matching_phone_ip(monkeypatch):
    monkeypatch.setattr("backend.adb_bridge.runtime_config.phone_tailscale_ip", lambda default="": "100.64.0.10")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.device_name", lambda default="Phone": "Phone")
    bridge = ADBBridge()
    devices = [
        {"serial": "100.64.0.20:5555", "state": "device", "transport": "wireless", "tail": "", "fields": {}},
        {"serial": "100.64.0.10:5555", "state": "device", "transport": "wireless", "tail": "", "fields": {}},
    ]
    assert bridge._pick_connected_target(devices) == ("100.64.0.10:5555", "wireless")


def test_pick_connected_target_prefers_name_match_when_multiple_usb(monkeypatch):
    monkeypatch.setattr("backend.adb_bridge.runtime_config.phone_tailscale_ip", lambda default="": "")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.device_name", lambda default="Phone": "Pixel 8")
    bridge = ADBBridge()
    devices = [
        {
            "serial": "USB123",
            "state": "device",
            "transport": "usb",
            "tail": "model:Nexus_5X",
            "fields": {"model": "Nexus_5X"},
        },
        {
            "serial": "USB456",
            "state": "device",
            "transport": "usb",
            "tail": "model:Pixel_8",
            "fields": {"model": "Pixel_8"},
        },
    ]
    assert bridge._pick_connected_target(devices) == ("USB456", "usb")


def test_connect_wireless_uses_phone_ip_candidate_without_explicit_target(monkeypatch):
    monkeypatch.setattr("backend.adb_bridge.runtime_config.adb_target", lambda default="": "")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.phone_tailscale_ip", lambda default="": "100.64.0.10")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.device_name", lambda default="Phone": "Phone")
    bridge = ADBBridge()
    calls = []

    def fake_run_adb(*args, timeout=8):
        calls.append(args)
        if args[:2] == ("connect", "100.64.0.10:5555"):
            return True, "connected to 100.64.0.10:5555\n"
        raise AssertionError(f"unexpected adb call: {args}")

    states = iter(
        [
            [{"serial": "100.64.0.10:5555", "state": "device", "transport": "wireless", "tail": "", "fields": {}}],
        ]
    )

    monkeypatch.setattr(bridge, "_run_adb", fake_run_adb)
    monkeypatch.setattr(bridge, "_get_devices", lambda force=False: next(states))
    assert bridge._connect_wireless() is True
    assert ("connect", "100.64.0.10:5555") in calls


def test_run_fails_over_from_bad_usb_target(monkeypatch):
    monkeypatch.setattr("backend.adb_bridge.runtime_config.adb_target", lambda default="": "")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.phone_tailscale_ip", lambda default="": "")
    monkeypatch.setattr("backend.adb_bridge.runtime_config.device_name", lambda default="Phone": "Phone")
    bridge = ADBBridge()
    commands = []

    monkeypatch.setattr(
        bridge,
        "_get_devices",
        lambda force=False: [
            {"serial": "USB123", "state": "device", "transport": "usb", "tail": "", "fields": {}},
            {"serial": "100.64.0.10:5555", "state": "device", "transport": "wireless", "tail": "", "fields": {}},
        ],
    )
    monkeypatch.setattr(bridge, "_ensure_wireless_keepalive_from_usb", lambda *_a, **_kw: None)

    def fake_run_adb(*args, timeout=8):
        commands.append(args)
        if args[:2] == ("-s", "USB123"):
            return False, "adb: device 'USB123' not found"
        if args[:4] == ("-s", "100.64.0.10:5555", "get-state"):
            return True, "device\n"
        if args[:2] == ("-s", "100.64.0.10:5555"):
            return True, "ok\n"
        raise AssertionError(f"unexpected adb call: {args}")

    monkeypatch.setattr(bridge, "_run_adb", fake_run_adb)

    ok, out = bridge._run("shell", "getprop", "gsm.call.state", timeout=2)
    assert ok is True
    assert out == "ok\n"
    assert bridge._is_bad_target("USB123") is True
    assert commands[0][:2] == ("-s", "USB123")
    assert commands[1][:2] == ("-s", "100.64.0.10:5555")
