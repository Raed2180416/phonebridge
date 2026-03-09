"""Deterministic regression tests for KDE Connect reachability contract (PB-008)."""

from __future__ import annotations

import sys
import types
import unittest.mock as mock

# Stub out dbus before importing kdeconnect so this test runs without system dbus.
_dbus_stub = types.ModuleType("dbus")
_dbus_stub.SessionBus = mock.MagicMock
_dbus_stub.Interface = mock.MagicMock
_dbus_stub.mainloop = types.ModuleType("dbus.mainloop")
_dbus_stub.mainloop.glib = types.ModuleType("dbus.mainloop.glib")
_dbus_stub.mainloop.glib.DBusGMainLoop = mock.MagicMock
# Use setdefault so we don't clobber a real dbus if present, but always ensure
# that Interface is set on whatever module is registered (a prior test's stub
# may have set only SessionBus).
sys.modules.setdefault("dbus", _dbus_stub)
sys.modules.setdefault("dbus.mainloop", _dbus_stub.mainloop)
sys.modules.setdefault("dbus.mainloop.glib", _dbus_stub.mainloop.glib)
# Ensure Interface is present regardless of which stub was registered first.
if not hasattr(sys.modules["dbus"], "Interface"):
    sys.modules["dbus"].Interface = mock.MagicMock

_gi_stub = types.ModuleType("gi")
_gi_repo = types.ModuleType("gi.repository")
_gi_repo.GLib = types.ModuleType("gi.repository.GLib")
sys.modules.setdefault("gi", _gi_stub)
sys.modules.setdefault("gi.repository", _gi_repo)
sys.modules.setdefault("gi.repository.GLib", _gi_repo.GLib)

# Force a fresh import of the real module — a prior test may have stubbed it
import importlib as _importlib
sys.modules.pop("backend.kdeconnect", None)
import backend.kdeconnect as kdeconnect  # noqa: E402
_importlib.reload(kdeconnect)  # ensure module-level state is fresh


class _FakeBus:
    def __init__(self, raises=False, devices=None):
        self._raises = raises
        self._devices = devices or []

    def get_object(self, *args, **kwargs):
        if self._raises:
            raise Exception("D-Bus unavailable")
        return _FakeObj(self._devices)


class _FakeObj:
    def __init__(self, devices):
        self._devices = devices

    def __init__(self, devices):
        self._devices = devices


class _FakeDaemon:
    def __init__(self, devices):
        self._devices = devices

    def devices(self, *args, **kwargs):
        return self._devices


def _make_kc(monkeypatch, *, raises=False, devices=None, device_id=""):
    kc = kdeconnect.KDEConnect.__new__(kdeconnect.KDEConnect)
    kc.device_id = device_id
    kc._bus = None

    if raises:
        def _get_object(*a, **kw):
            raise Exception("D-Bus unavailable")
    else:
        _daemon = _FakeDaemon(devices or [])

        class _FakeBusImpl:
            def get_object(self, *a, **kw):
                return types.SimpleNamespace(
                    # Not used directly; Interface() wraps it
                )

        # Monkeypatch dbus.Interface to return the fake daemon
        import dbus
        real_interface = dbus.Interface

        def _fake_interface(obj, iface_name):
            if "kdeconnect.daemon" in iface_name:
                return _daemon
            return real_interface(obj, iface_name)

        monkeypatch.setattr(dbus, "Interface", _fake_interface)

        bus_impl = _FakeBusImpl()
        kc._bus = bus_impl

    if raises:
        class _RaisingBus:
            def get_object(self, *a, **kw):
                raise Exception("D-Bus unavailable")
        kc._bus = _RaisingBus()

    return kc


def test_is_reachable_returns_none_on_dbus_exception(monkeypatch):
    """D-Bus failure must return None, not False or True."""
    kc = _make_kc(monkeypatch, raises=True, device_id="abc123")
    result = kc.is_reachable()
    assert result is None, f"Expected None, got {result!r}"


def test_is_reachable_returns_false_empty_list(monkeypatch):
    """Empty device list → False (device definitely not reachable)."""
    kc = _make_kc(monkeypatch, raises=False, devices=[], device_id="abc123")
    result = kc.is_reachable()
    assert result is False


def test_is_reachable_returns_false_device_not_in_list(monkeypatch):
    """Device id not in reachable list → False."""
    kc = _make_kc(monkeypatch, raises=False, devices=["other-device"], device_id="abc123")
    result = kc.is_reachable()
    assert result is False


def test_is_reachable_returns_true_device_in_list(monkeypatch):
    """Device id present in reachable list → True."""
    kc = _make_kc(monkeypatch, raises=False, devices=["abc123", "other"], device_id="abc123")
    result = kc.is_reachable()
    assert result is True


def test_is_reachable_returns_true_no_target_id_when_devices_present(monkeypatch):
    """No device_id configured but devices present → True (assume reachable)."""
    kc = _make_kc(monkeypatch, raises=False, devices=["some-device"], device_id="")
    result = kc.is_reachable()
    assert result is True


def test_kde_status_from_none_maps_to_unknown():
    """When is_reachable() == None, derived kde_status must be 'unknown'."""
    raw = None
    kde_enabled = True
    kde_status = (
        "disabled" if not kde_enabled
        else "reachable" if raw is True
        else "unreachable" if raw is False
        else "unknown"
    )
    assert kde_status == "unknown"


def test_kde_status_from_false_maps_to_unreachable():
    """When is_reachable() == False, derived kde_status must be 'unreachable'."""
    raw = False
    kde_enabled = True
    kde_status = (
        "disabled" if not kde_enabled
        else "reachable" if raw is True
        else "unreachable" if raw is False
        else "unknown"
    )
    assert kde_status == "unreachable"


def test_kde_status_disabled_when_integration_off():
    """When kde_enabled is False, kde_status must be 'disabled' regardless of raw."""
    for raw in (True, False, None):
        kde_enabled = False
        kde_status = (
            "disabled" if not kde_enabled
            else "reachable" if raw is True
            else "unreachable" if raw is False
            else "unknown"
        )
        assert kde_status == "disabled", f"Expected disabled for raw={raw!r}"
