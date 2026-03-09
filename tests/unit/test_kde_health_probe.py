"""Deterministic tests for KDE reconnect watchdog improvements:
- trigger_refresh() calls kdeconnect-cli --refresh and returns correct bool
- kde_health_probe() returns 'ok' when device is reachable on first check
- kde_health_probe() attempts refresh + re-probe when not reachable
- kde_health_probe() returns 'degraded' when device still unreachable after refresh
- kde_health_probe() returns 'unknown' when D-Bus raises on both checks
- kde_health_probe() returns 'ok' if second check is True after refresh
- Probe result includes required keys: status, reachable, refresh_ok, checked_at, device_id
"""

from __future__ import annotations

import sys
import types
import unittest.mock as mock

# ── dbus / gi stubs ───────────────────────────────────────────────────────────
for _mod in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

_dbus = sys.modules["dbus"]
if not hasattr(_dbus, "SessionBus"):
    _dbus.SessionBus = lambda: None
if not hasattr(_dbus, "Interface"):
    _dbus.Interface = mock.MagicMock
if not hasattr(_dbus, "mainloop"):
    _dbus.mainloop = sys.modules["dbus.mainloop"]

_gi_mod = sys.modules["gi"]
_gi_repo = sys.modules["gi.repository"]
if not hasattr(_gi_mod, "require_version"):
    _gi_mod.require_version = lambda *a, **kw: None
if not hasattr(_gi_repo, "GLib"):
    _gi_repo.GLib = types.SimpleNamespace(MainLoop=object)
sys.modules.setdefault("dbus.mainloop.glib", types.ModuleType("dbus.mainloop.glib"))
if not hasattr(sys.modules["dbus.mainloop.glib"], "DBusGMainLoop"):
    sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda **kw: None

# ── settings_store stub ───────────────────────────────────────────────────────
_settings_mod = types.ModuleType("backend.settings_store")
_settings_mod.get = lambda k, default=None: {"device_id": "testdev123"}.get(k, default)
_settings_mod.set = lambda k, v: None
sys.modules["backend.settings_store"] = _settings_mod

# ── Force fresh import of real kdeconnect module ──────────────────────────────
import importlib as _importlib
sys.modules.pop("backend.kdeconnect", None)
import backend.kdeconnect as kc
_importlib.reload(kc)

# Save the real class before any patching so __new__ always works.
_RealKDEConnect = kc.KDEConnect


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_kc_reachable(returns: bool | None):
    """Build a KDEConnect instance whose is_reachable() returns a fixed value."""
    inst = _RealKDEConnect.__new__(_RealKDEConnect)
    inst.device_id = "testdev123"
    inst._bus = None
    inst.is_reachable = lambda: returns
    return inst


# ── trigger_refresh tests ─────────────────────────────────────────────────────

def test_trigger_refresh_returns_true_on_zero_exit(monkeypatch):
    """trigger_refresh returns True when kdeconnect-cli exits 0."""
    fake = mock.MagicMock()
    fake.returncode = 0
    monkeypatch.setattr(kc.subprocess, "run", lambda *a, **kw: fake)
    assert kc.trigger_refresh() is True


def test_trigger_refresh_returns_false_on_nonzero_exit(monkeypatch):
    """trigger_refresh returns False when kdeconnect-cli exits non-zero."""
    fake = mock.MagicMock()
    fake.returncode = 1
    fake.stderr = "error"
    monkeypatch.setattr(kc.subprocess, "run", lambda *a, **kw: fake)
    assert kc.trigger_refresh() is False


def test_trigger_refresh_returns_false_on_exception(monkeypatch):
    """trigger_refresh returns False when subprocess raises."""
    def _raise(*a, **kw):
        raise FileNotFoundError("kdeconnect-cli not found")
    monkeypatch.setattr(kc.subprocess, "run", _raise)
    assert kc.trigger_refresh() is False


# ── kde_health_probe tests ────────────────────────────────────────────────────

def test_health_probe_ok_when_reachable_on_first_check(monkeypatch):
    """When device is reachable immediately, status='ok' and no refresh."""
    monkeypatch.setattr(kc, "KDEConnect", lambda: _make_kc_reachable(True))
    monkeypatch.setattr(kc, "trigger_refresh", lambda: False)  # must not be called

    result = kc.kde_health_probe("testdev123")
    assert result["status"] == "ok"
    assert result["reachable"] is True
    assert result["refresh_ok"] is None  # refresh not attempted
    assert "checked_at" in result
    assert result["device_id"] == "testdev123"


def test_health_probe_ok_after_refresh(monkeypatch):
    """Not reachable first, refresh triggered, second check True → status='ok'."""
    _calls = {"n": 0}

    def _factory():
        inst = _RealKDEConnect.__new__(_RealKDEConnect)
        inst.device_id = "testdev123"
        inst._bus = None
        _calls["n"] += 1
        n = _calls["n"]
        inst.is_reachable = lambda: False if n == 1 else True
        return inst

    monkeypatch.setattr(kc, "KDEConnect", _factory)
    monkeypatch.setattr(kc, "trigger_refresh", lambda: True)

    result = kc.kde_health_probe("testdev123")
    assert result["status"] == "ok"
    assert result["reachable"] is True
    assert result["refresh_ok"] is True


def test_health_probe_degraded_still_unreachable_after_refresh(monkeypatch):
    """Unreachable before and after refresh → status='degraded'."""
    monkeypatch.setattr(kc, "KDEConnect", lambda: _make_kc_reachable(False))
    monkeypatch.setattr(kc, "trigger_refresh", lambda: False)

    result = kc.kde_health_probe("testdev123")
    assert result["status"] == "degraded"
    assert result["reachable"] is False
    assert result["refresh_ok"] is False


def test_health_probe_unknown_when_dbus_unavailable(monkeypatch):
    """D-Bus raises on both checks → status='unknown'."""
    def _raising_factory():
        inst = _RealKDEConnect.__new__(_RealKDEConnect)
        inst.device_id = "testdev123"
        inst._bus = None
        inst.is_reachable = lambda: None  # contract: None = D-Bus unavailable
        return inst

    monkeypatch.setattr(kc, "KDEConnect", _raising_factory)
    monkeypatch.setattr(kc, "trigger_refresh", lambda: False)

    result = kc.kde_health_probe("testdev123")
    assert result["status"] == "unknown"
    assert result["reachable"] is None


def test_health_probe_result_has_required_keys(monkeypatch):
    """All required keys are always present in the result."""
    monkeypatch.setattr(kc, "KDEConnect", lambda: _make_kc_reachable(True))
    monkeypatch.setattr(kc, "trigger_refresh", lambda: True)

    result = kc.kde_health_probe()
    for key in ("status", "reachable", "refresh_ok", "checked_at", "device_id"):
        assert key in result, f"Missing key: {key}"
    assert isinstance(result["checked_at"], int)
    assert result["checked_at"] > 0


def test_health_probe_unknown_when_kde_connect_constructor_raises(monkeypatch):
    """If KDEConnect() itself raises, result is status='unknown'."""
    _calls = {"n": 0}

    def _raising():
        _calls["n"] += 1
        raise RuntimeError("D-Bus session not available")

    monkeypatch.setattr(kc, "KDEConnect", _raising)
    monkeypatch.setattr(kc, "trigger_refresh", lambda: False)

    result = kc.kde_health_probe("testdev123")
    assert result["status"] == "unknown"
    assert result["reachable"] is None
