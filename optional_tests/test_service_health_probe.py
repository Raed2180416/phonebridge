"""Tests for backend/health.py — self-healing multi-service health probe.

Cause:  No unified service health check; KDE-only probe existed but ADB,
        Syncthing, and Tailscale had no comparable health/self-heal logic.
Fix:    backend/health.py — four independent probes with recovery actions;
        aggregates to state["service_health"]; schedule_probe() is non-blocking.
"""
from __future__ import annotations

import sys
import types
import time

import pytest

# ── Stubs that must NOT be installed at module level ─────────────────────────
# (Lesson learnt: module-level sys.modules writes pollute collection of later
#  test files that do `import backend.settings_store` at their own module level.)

# build stub objects
_state_data: dict = {}
_state_mod = types.ModuleType("backend.state")
_state_mod.state = type("_S", (), {
    "get":       lambda self, k, d=None: _state_data.get(k, d),
    "set":       lambda self, k, v: _state_data.__setitem__(k, v),
    "subscribe": lambda self, k, cb: None,
})()

import backend as _backend_pkg

_STUB_KEYS = [
    "backend.state",
    "backend.kdeconnect",
    "backend.adb_bridge",
    "backend.syncthing",
    "backend.tailscale",
    "backend.health",
    "backend.settings_store",
]


@pytest.fixture(autouse=True, scope="module")
def _install_and_restore_stubs():
    """Install minimal stubs; restore originals after all module tests finish."""
    saved_sys = {k: sys.modules.get(k) for k in _STUB_KEYS}
    saved_ss = _backend_pkg.__dict__.get("settings_store")

    # Minimal settings stub so health.py imports don't fail
    _ss = types.ModuleType("backend.settings_store")
    _ss.get = lambda k, d=None: d  # type: ignore[attr-defined]
    sys.modules["backend.settings_store"] = _ss
    _backend_pkg.settings_store = _ss  # type: ignore[attr-defined]

    sys.modules["backend.state"] = _state_mod

    yield

    # Restore
    for k, v in saved_sys.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v
    if saved_ss is None:
        _backend_pkg.__dict__.pop("settings_store", None)
    else:
        _backend_pkg.settings_store = saved_ss  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_state():
    _state_data.clear()
    yield


# ── Helpers to build fake probe results ──────────────────────────────────────

def _ok_kde():
    return {"status": "ok",   "reachable": True, "refresh_ok": None, "checked_at": 1}

def _degraded_kde():
    return {"status": "degraded", "reachable": False, "refresh_ok": True, "checked_at": 1}

def _unknown_kde():
    return {"status": "unknown", "reachable": None, "refresh_ok": None, "checked_at": 1}


# ── Tests for _probe_kde ──────────────────────────────────────────────────────

def test_probe_kde_ok(monkeypatch):
    """_probe_kde returns status=ok when kde_health_probe returns ok."""
    import importlib
    # Provide a fake kdeconnect module
    kc_mod = types.ModuleType("backend.kdeconnect")
    kc_mod.kde_health_probe = lambda device_id="": _ok_kde()  # type: ignore[attr-defined]
    kc_mod.DEVICE_ID = ""  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", kc_mod)

    # Force reload health so it picks up the new stub
    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_kde()
    assert result["status"] == "ok"
    assert result["reachable"] is True


def test_probe_kde_degraded(monkeypatch):
    """_probe_kde returns status=degraded when kde_health_probe returns degraded."""
    kc_mod = types.ModuleType("backend.kdeconnect")
    kc_mod.kde_health_probe = lambda device_id="": _degraded_kde()  # type: ignore[attr-defined]
    kc_mod.DEVICE_ID = ""  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", kc_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_kde()
    assert result["status"] == "degraded"
    assert result["reachable"] is False


def test_probe_kde_exception_returns_unknown(monkeypatch):
    """_probe_kde catches exceptions and returns status=unknown."""
    kc_mod = types.ModuleType("backend.kdeconnect")
    def _raise(*a, **kw): raise RuntimeError("D-Bus crash")
    kc_mod.kde_health_probe = _raise  # type: ignore[attr-defined]
    kc_mod.DEVICE_ID = ""  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", kc_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_kde()
    assert result["status"] == "unknown"
    assert result["reachable"] is None


# ── Tests for _probe_syncthing ────────────────────────────────────────────────

def test_probe_syncthing_ok(monkeypatch):
    """_probe_syncthing returns status=ok on successful ping."""
    st_mod = types.ModuleType("backend.syncthing")
    class _FakeST:
        def ping_status(self, timeout=4): return True, 200, "ok"
    st_mod.SyncthingClient = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.syncthing", st_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_syncthing()
    assert result["status"] == "ok"
    assert result["ping_ok"] is True
    assert result["http_status"] == 200


def test_probe_syncthing_degraded_on_http_error(monkeypatch):
    """_probe_syncthing returns degraded when ping returns non-200."""
    st_mod = types.ModuleType("backend.syncthing")
    class _FakeST:
        def ping_status(self, timeout=4): return False, 503, "http_error"
    st_mod.SyncthingClient = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.syncthing", st_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_syncthing()
    assert result["status"] == "degraded"
    assert result["ping_ok"] is False


def test_probe_syncthing_unknown_when_no_api_key(monkeypatch):
    """Missing API key is not degraded — it's a config gap (unknown)."""
    st_mod = types.ModuleType("backend.syncthing")
    class _FakeST:
        def ping_status(self, timeout=4): return False, None, "missing_api_key"
    st_mod.SyncthingClient = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.syncthing", st_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_syncthing()
    assert result["status"] == "unknown"


# ── Tests for _probe_tailscale ────────────────────────────────────────────────

def test_probe_tailscale_ok(monkeypatch):
    """_probe_tailscale returns ok when Tailscale reports Running."""
    ts_mod = types.ModuleType("backend.tailscale")
    class _FakeTS:
        def get_status(self): return {"BackendState": "Running"}
    ts_mod.TailscaleManager = _FakeTS  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.tailscale", ts_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_tailscale()
    assert result["status"] == "ok"
    assert result["backend"] == "running"


def test_probe_tailscale_degraded_when_stopped(monkeypatch):
    """_probe_tailscale returns degraded when BackendState is Stopped."""
    ts_mod = types.ModuleType("backend.tailscale")
    class _FakeTS:
        def get_status(self): return {"BackendState": "Stopped"}
    ts_mod.TailscaleManager = _FakeTS  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.tailscale", ts_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    result = h._probe_tailscale()
    assert result["status"] == "degraded"


# ── Tests for probe_all_services aggregation ─────────────────────────────────

def _load_health_with_probe_stubs(monkeypatch, kde_status, adb_connected, syncthing_ok, ts_backend):
    """Load backend.health after installing per-probe stubs via monkeypatch."""
    # KDE
    kc_mod = types.ModuleType("backend.kdeconnect")
    kc_mod.kde_health_probe = lambda device_id="": {  # type: ignore[attr-defined]
        "status": kde_status,
        "reachable": kde_status == "ok",
        "refresh_ok": None,
        "checked_at": 1,
    }
    kc_mod.DEVICE_ID = ""  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", kc_mod)

    # ADB
    adb_mod = types.ModuleType("backend.adb_bridge")
    class _FakeADB:
        def is_connected(self): return adb_connected
        def connect_wifi(self): pass
    adb_mod.ADBBridge = _FakeADB  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.adb_bridge", adb_mod)

    # Syncthing
    st_mod = types.ModuleType("backend.syncthing")
    class _FakeST:
        def ping_status(self, timeout=4): return syncthing_ok, (200 if syncthing_ok else 503), ("ok" if syncthing_ok else "http_error")
    st_mod.SyncthingClient = _FakeST  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.syncthing", st_mod)

    # Tailscale
    ts_mod = types.ModuleType("backend.tailscale")
    class _FakeTS:
        def get_status(self): return {"BackendState": ts_backend}
    ts_mod.TailscaleManager = _FakeTS  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.tailscale", ts_mod)

    sys.modules.pop("backend.health", None)
    import backend.health as h
    return h


def test_probe_all_services_all_ok(monkeypatch):
    """probe_all_services returns overall=ok when every service is healthy."""
    h = _load_health_with_probe_stubs(monkeypatch, "ok", True, True, "Running")
    result = h.probe_all_services()
    assert result["overall"] == "ok"
    assert result["kde"]["status"] == "ok"
    assert result["adb"]["status"] == "ok"
    assert result["syncthing"]["status"] == "ok"
    assert result["tailscale"]["status"] == "ok"
    assert "probed_at" in result


def test_probe_all_services_degraded_when_any_degraded(monkeypatch):
    """overall=degraded when at least one service is degraded."""
    h = _load_health_with_probe_stubs(monkeypatch, "degraded", True, True, "Running")
    result = h.probe_all_services()
    assert result["overall"] == "degraded"


def test_probe_all_services_unknown_when_no_degraded_but_some_unknown(monkeypatch):
    """overall=unknown when no degraded services but some are unknown."""
    h = _load_health_with_probe_stubs(monkeypatch, "unknown", True, True, "Running")
    result = h.probe_all_services()
    # adb ok, syncthing ok, tailscale ok, kde unknown → overall unknown
    assert result["overall"] == "unknown"


# ── Test schedule_probe writes to state ──────────────────────────────────────

def test_schedule_probe_writes_service_health(monkeypatch):
    """schedule_probe() spawns thread; state['service_health'] is populated."""
    h = _load_health_with_probe_stubs(monkeypatch, "ok", True, True, "Running")

    # Wire our state stub
    monkeypatch.setitem(sys.modules, "backend.state", _state_mod)

    h.schedule_probe()

    # Give the daemon thread time to run
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if _state_data.get("service_health"):
            break
        time.sleep(0.05)

    sh = _state_data.get("service_health", {})
    assert sh.get("overall") == "ok"
    assert "kde" in sh
    assert "adb" in sh
    assert "syncthing" in sh
    assert "tailscale" in sh
