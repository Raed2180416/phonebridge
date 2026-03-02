"""Tests for BT headphone audio gap fix:
- Watchdog starts when call route becomes active
- Watchdog stops when call route ends / fails
- Watchdog re-triggers sync_result on mic path drop
- Watchdog exits cleanly when call_audio_active becomes False between polls
"""

import sys
import types
import threading
import time

# ── Minimal dbus/gi stubs ────────────────────────────────────────────────────
for mod_name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
_dbus = sys.modules["dbus"]
_dbus.SessionBus = lambda: None
gi_mod = sys.modules["gi"]
gi_repo = sys.modules["gi.repository"]
gi_mod.repository = gi_repo
gi_repo.GLib = types.SimpleNamespace(MainLoop=object)
gi_mod.require_version = lambda *a, **kw: None
sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda **kw: None

# ── Stub all audio_route dependencies ────────────────────────────────────────
_state_data: dict = {"call_audio_active": False}
_state_subs: dict = {}

class _State:
    def get(self, key, default=None): return _state_data.get(key, default)
    def set(self, key, val): _state_data[key] = val
    def subscribe(self, key, cb): _state_subs.setdefault(key, []).append(cb)

_state_mod = types.ModuleType("backend.state")
_state_mod.state = _State()
sys.modules["backend.state"] = _state_mod

_settings_mod = types.ModuleType("backend.settings_store")
_settings_data = {"audio_redirect": False, "bt_call_ready_mode": False}
_settings_mod.get = lambda k, default=None: _settings_data.get(k, default)
_settings_mod.set = lambda k, v: _settings_data.__setitem__(k, v)
sys.modules["backend.settings_store"] = _settings_mod

_adb_mod = types.ModuleType("backend.adb_bridge")
_adb_mod.ADBBridge = lambda *a, **kw: None
sys.modules["backend.adb_bridge"] = _adb_mod

# ── Import module under test ──────────────────────────────────────────────────
import importlib
import backend.audio_route as ar


# ── Helpers ──────────────────────────────────────────────────────────────────

def _reset():
    """Reset module-level watchdog state and call_audio_active."""
    ar._stop_call_route_watchdog()
    # Give thread a moment to exit
    if ar._watchdog_thread is not None:
        ar._watchdog_thread.join(timeout=1.0)
    ar._watchdog_thread = None
    ar._watchdog_stop.clear()
    _state_data["call_audio_active"] = False


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_watchdog_starts_on_active_result(monkeypatch):
    """_call_route_active_result starts the watchdog thread."""
    _reset()
    monkeypatch.setattr(ar, "_boost_call_mic_gain", lambda: None)
    monkeypatch.setattr(ar, "active_backend", lambda: "bt")
    monkeypatch.setattr(ar, "_set_call_route_state", lambda *a, **kw: None)
    # Stub call_audio to avoid import — use monkeypatch so it is restored
    import types as _t
    ca = _t.ModuleType("backend.call_audio")
    ca.apply_saved_settings = lambda: None
    ca.session_active = lambda: False
    monkeypatch.setitem(sys.modules, "backend.call_audio", ca)

    ar._call_route_active_result("test reason")
    # Watchdog thread should be alive
    assert ar._watchdog_thread is not None
    assert ar._watchdog_thread.is_alive()
    _reset()


def test_watchdog_stops_on_failed_result(monkeypatch):
    """_call_route_failed_result stops the watchdog."""
    _reset()
    monkeypatch.setattr(ar, "_boost_call_mic_gain", lambda: None)
    monkeypatch.setattr(ar, "active_backend", lambda: "bt")
    monkeypatch.setattr(ar, "_set_call_route_state", lambda *a, **kw: None)
    import types as _t
    ca = _t.ModuleType("backend.call_audio")
    ca.apply_saved_settings = lambda: None
    ca.session_active = lambda: False
    monkeypatch.setitem(sys.modules, "backend.call_audio", ca)

    # Start watchdog first
    ar._call_route_active_result("started")
    assert ar._watchdog_thread is not None and ar._watchdog_thread.is_alive()

    # Now fail it
    ar._call_route_failed_result("test failure")
    # Stop event should be set
    assert ar._watchdog_stop.is_set()
    _reset()


def test_watchdog_stops_on_stop_call(monkeypatch):
    """ar.stop() stops the watchdog."""
    _reset()
    monkeypatch.setattr(ar, "_stop_proc", lambda: True)
    monkeypatch.setattr(ar, "_enforce_call_ready_bt_mode", lambda: None)
    monkeypatch.setattr(ar, "_restore_call_audio_session_if_needed", lambda: None)

    # Manually start watchdog
    ar._start_call_route_watchdog()
    assert ar._watchdog_thread is not None and ar._watchdog_thread.is_alive()

    ar.stop()
    assert ar._watchdog_stop.is_set()
    _reset()


def test_watchdog_exits_when_call_audio_active_false():
    """Watchdog loop exits when call_audio_active becomes False between polls."""
    _reset()
    _state_data["call_audio_active"] = True

    # Shorten interval for test speed
    original_interval = ar._WATCHDOG_INTERVAL_S
    ar._WATCHDOG_INTERVAL_S = 0.05

    # Mic path check always returns True so no re-sync is triggered
    import unittest.mock as mock
    _fake_state = _state_mod.state  # the _State() bound to _state_data
    with mock.patch.object(ar, "_bt_call_mic_path_active", return_value=True), \
         mock.patch.object(ar, "state", _fake_state):
        ar._start_call_route_watchdog()
        time.sleep(0.02)
        _state_data["call_audio_active"] = False  # signal exit
        ar._watchdog_thread.join(timeout=2.0)
        assert not ar._watchdog_thread.is_alive(), "watchdog should have exited"

    ar._WATCHDOG_INTERVAL_S = original_interval
    _reset()


def test_watchdog_resyncs_on_mic_drop(monkeypatch):
    """Watchdog calls sync_result when mic path drops mid-call."""
    _reset()
    _state_data["call_audio_active"] = True

    ar._WATCHDOG_INTERVAL_S = 0.05
    sync_calls = []

    import unittest.mock as mock

    # First check returns True (mic up), subsequent checks return False (drop).
    mic_responses = iter([True, False, False, False])

    def _fake_mic():
        try:
            return next(mic_responses)
        except StopIteration:
            return False

    fake_result = types.SimpleNamespace(ok=True, backend="bt", status="active", reason="ok")

    def _fake_sync(**kw):
        sync_calls.append(kw)
        _state_data["call_audio_active"] = False  # stop watchdog after re-sync
        return fake_result

    _fake_state = _state_mod.state  # the _State() bound to _state_data
    with mock.patch.object(ar, "_bt_call_mic_path_active", side_effect=_fake_mic), \
         mock.patch.object(ar, "sync_result", side_effect=_fake_sync), \
         mock.patch.object(ar, "state", _fake_state):
        ar._start_call_route_watchdog()
        # Allow at least 3 poll intervals
        ar._watchdog_thread.join(timeout=3.0)

    assert len(sync_calls) >= 1, "sync_result should have been called on mic drop"
    ar._WATCHDOG_INTERVAL_S = 0.05
    _reset()
