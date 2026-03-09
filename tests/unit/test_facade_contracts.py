"""Facade delegation tests for the split ADB and KDE helper modules."""

from __future__ import annotations

import importlib
import sys
import types


def test_adb_bridge_delegates_telephony_and_media_helpers(monkeypatch):
    import backend.adb_bridge as adb_bridge

    bridge = adb_bridge.ADBBridge(target="serial")
    telephony_calls = []
    media_calls = []

    monkeypatch.setattr(
        adb_bridge.adb_telephony,
        "get_call_state_fast",
        lambda current: telephony_calls.append(current) or "idle",
    )
    monkeypatch.setattr(
        adb_bridge.adb_media,
        "launch_scrcpy",
        lambda current, **kwargs: media_calls.append((current, kwargs)) or "proc",
    )

    assert bridge.get_call_state_fast() == "idle"
    assert bridge.launch_scrcpy(mode="audio_output") == "proc"
    assert telephony_calls == [bridge]
    assert media_calls == [(bridge, {"mode": "audio_output", "extra_args": None, "env_overrides": None})]


def test_kdeconnect_delegates_notification_and_signal_helpers(monkeypatch):
    for mod_name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)

    _dbus = sys.modules["dbus"]
    _dbus.SessionBus = lambda: None
    _dbus.Interface = lambda *a, **kw: None
    sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda set_as_default=False: None
    _gi_mod = sys.modules["gi"]
    _gi_repo = sys.modules["gi.repository"]
    _gi_mod.repository = _gi_repo
    _gi_repo.GLib = types.SimpleNamespace(MainLoop=object)

    sys.modules.pop("backend.kdeconnect", None)
    import backend.kdeconnect as kdeconnect

    kdeconnect = importlib.reload(kdeconnect)

    monkeypatch.setattr(kdeconnect.KDEConnect, "_refresh_device_binding", lambda self: None)
    kc = kdeconnect.KDEConnect()
    notif_calls = []
    signal_calls = []

    monkeypatch.setattr(
        kdeconnect.kde_notifications,
        "get_notifications",
        lambda current: notif_calls.append(current) or [{"id": "n1"}],
    )
    monkeypatch.setattr(
        kdeconnect.kde_signals,
        "connect_notification_signal",
        lambda current, posted, removed, updated, all_removed: signal_calls.append(
            (current, posted, removed, updated, all_removed)
        ) or True,
    )

    assert kc.get_notifications() == [{"id": "n1"}]
    assert kc.connect_notification_signal("posted") is True
    assert notif_calls == [kc]
    assert signal_calls == [(kc, "posted", None, None, None)]
