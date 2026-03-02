"""Unified self-healing health probe for all PhoneBridge services.

Probes KDE Connect, ADB, Syncthing, and Tailscale independently.  On a
degraded result each probe attempts a lightweight recovery action before
returning its final status.  Results are written to state["service_health"].

Schema of state["service_health"]::

    {
        "kde":       {"status": "ok"|"degraded"|"unknown", "reachable": bool|None,
                      "refresh_ok": bool|None, "checked_at": int_ms},
        "adb":       {"status": "ok"|"degraded"|"unknown", "connected": bool|None,
                      "checked_at": int_ms},
        "syncthing": {"status": "ok"|"degraded"|"unknown", "ping_ok": bool|None,
                      "http_status": int|None, "checked_at": int_ms},
        "tailscale": {"status": "ok"|"degraded"|"unknown", "backend": str,
                      "checked_at": int_ms},
        "overall":   "ok" | "degraded" | "unknown",
        "probed_at": int_ms,
    }

Usage::

    from backend.health import probe_all_services
    result = probe_all_services()          # blocking — call from worker thread
    # or use the non-blocking convenience:
    from backend.health import schedule_probe
    schedule_probe()                       # spawns daemon thread; writes to state
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# ── Individual service probes ─────────────────────────────────────────────────

def _probe_kde(device_id: str = "") -> dict[str, Any]:
    """Probe KDE Connect reachability; attempt --refresh on first failure."""
    from backend.kdeconnect import kde_health_probe
    try:
        r = kde_health_probe(device_id)
        return {
            "status":       r.get("status", "unknown"),
            "reachable":    r.get("reachable"),
            "refresh_ok":   r.get("refresh_ok"),
            "checked_at":   r.get("checked_at", _now_ms()),
        }
    except Exception as exc:
        log.debug("health._probe_kde failed: %s", exc)
        return {"status": "unknown", "reachable": None, "refresh_ok": None, "checked_at": _now_ms()}


def _probe_adb() -> dict[str, Any]:
    """Probe ADB device presence; attempt wifi reconnect on first failure."""
    try:
        from backend.adb_bridge import ADBBridge
        bridge = ADBBridge()
        connected = bridge.is_connected()
        if connected:
            return {"status": "ok", "connected": True, "checked_at": _now_ms()}

        # Recovery: try wifi reconnect once
        log.debug("health._probe_adb: device not connected, attempting reconnect")
        try:
            bridge.connect_wifi()
        except Exception as exc:
            log.debug("health._probe_adb: connect_wifi failed: %s", exc)

        connected2 = bridge.is_connected()
        return {
            "status":     "ok" if connected2 else "degraded",
            "connected":  connected2,
            "checked_at": _now_ms(),
        }
    except Exception as exc:
        log.debug("health._probe_adb failed: %s", exc)
        return {"status": "unknown", "connected": None, "checked_at": _now_ms()}


def _probe_syncthing() -> dict[str, Any]:
    """Probe Syncthing REST API ping."""
    try:
        from backend.syncthing import SyncthingClient
        st = SyncthingClient()
        ok, http_status, detail = st.ping_status(timeout=4)
        if ok:
            return {
                "status":      "ok",
                "ping_ok":     True,
                "http_status": http_status,
                "checked_at":  _now_ms(),
            }
        # degraded unless API key is simply missing (that's a config issue, not service down)
        if detail == "missing_api_key":
            svc_status = "unknown"
        else:
            svc_status = "degraded"
        return {
            "status":      svc_status,
            "ping_ok":     False,
            "http_status": http_status,
            "checked_at":  _now_ms(),
        }
    except Exception as exc:
        log.debug("health._probe_syncthing failed: %s", exc)
        return {"status": "unknown", "ping_ok": None, "http_status": None, "checked_at": _now_ms()}


def _probe_tailscale() -> dict[str, Any]:
    """Probe Tailscale daemon state."""
    try:
        from backend.tailscale import TailscaleManager
        ts = TailscaleManager()
        status = ts.get_status()
        # get_status returns a dict with "backend_state" like "Running", "Stopped", etc.
        backend = str(status.get("backend_state", "") or status.get("BackendState", "") or "").lower()
        if backend == "running":
            svc_status = "ok"
        elif backend in ("", "unknown"):
            svc_status = "unknown"
        else:
            svc_status = "degraded"
        return {
            "status":     svc_status,
            "backend":    backend,
            "checked_at": _now_ms(),
        }
    except Exception as exc:
        log.debug("health._probe_tailscale failed: %s", exc)
        return {"status": "unknown", "backend": "", "checked_at": _now_ms()}


# ── Aggregator ────────────────────────────────────────────────────────────────

def probe_all_services(device_id: str = "") -> dict[str, Any]:
    """Run all four service probes and return a consolidated health dict.

    This function is blocking.  Call from a daemon thread or via
    :func:`schedule_probe`.
    """
    kde        = _probe_kde(device_id)
    adb        = _probe_adb()
    syncthing  = _probe_syncthing()
    tailscale  = _probe_tailscale()

    statuses = [kde["status"], adb["status"], syncthing["status"], tailscale["status"]]
    if any(s == "degraded" for s in statuses):
        overall = "degraded"
    elif all(s == "ok" for s in statuses):
        overall = "ok"
    else:
        overall = "unknown"

    result: dict[str, Any] = {
        "kde":        kde,
        "adb":        adb,
        "syncthing":  syncthing,
        "tailscale":  tailscale,
        "overall":    overall,
        "probed_at":  _now_ms(),
    }

    if overall == "degraded":
        degraded_svcs = [k for k in ("kde", "adb", "syncthing", "tailscale")
                         if result[k]["status"] == "degraded"]
        log.warning("Service health: %s degraded — %s", overall, degraded_svcs)
    else:
        log.debug("Service health: %s", overall)

    return result


# ── Non-blocking convenience ──────────────────────────────────────────────────

def schedule_probe(device_id: str = "") -> None:
    """Spawn a daemon thread that calls probe_all_services() and writes the
    result to state["service_health"].

    Safe to call from the Qt main thread.
    """
    def _job() -> None:
        try:
            result = probe_all_services(device_id)
            from backend.state import state  # late import to avoid circular dep
            state.set("service_health", result)
        except Exception:
            log.exception("schedule_probe job failed")

    threading.Thread(target=_job, daemon=True, name="pb-health-probe").start()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)
