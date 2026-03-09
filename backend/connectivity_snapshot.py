"""Shared connectivity/syncthing snapshot collection for UI refresh workers."""
from __future__ import annotations

import threading
import time

import backend.settings_store as settings
from backend.adb_bridge import ADBBridge
from backend.kdeconnect import KDEConnect
from backend import runtime_config
from backend.syncthing import Syncthing
from backend.tailscale import Tailscale


_SYNCTHING_STABILIZE_LOCK = threading.Lock()
_LAST_SYNCTHING_STABILIZE_ATTEMPT = 0.0


def _maybe_heal_phone_identity(snapshot: dict) -> None:
    matched_phone_ip = str(snapshot.get("phone_ip") or "").strip()
    if not matched_phone_ip:
        return
    configured_phone_ip = runtime_config.phone_tailscale_ip()
    configured_adb_target = runtime_config.adb_target()
    healed_target = f"{matched_phone_ip}:5555"
    updates = {}
    if matched_phone_ip != configured_phone_ip:
        updates["phone_tailscale_ip"] = matched_phone_ip
    if (
        (not configured_adb_target)
        or (configured_phone_ip and configured_adb_target.startswith(configured_phone_ip + ":"))
    ) and configured_adb_target != healed_target:
        updates["adb_target"] = healed_target
    if updates:
        settings.set_many(updates)


def _collect_syncthing_runtime(st: Syncthing, *, auto_stabilize: bool = True) -> dict:
    global _LAST_SYNCTHING_STABILIZE_ATTEMPT
    try:
        status = st.get_runtime_status(timeout=3)
    except Exception:
        status = {
            "service_active": False,
            "api_reachable": False,
            "reason": "status_unavailable",
            "unit_state": "unknown",
            "unit_file_state": "unknown",
        }
    service_active = bool(status.get("service_active", False))
    api_reachable = bool(status.get("api_reachable", False))
    reason = str(status.get("reason") or "unknown")
    unit_state = str(status.get("unit_state") or "unknown")
    unit_file_state = str(status.get("unit_file_state") or "unknown")
    if auto_stabilize and (not service_active) and unit_file_state != "masked" and reason in {
        "unit_inactive_api_reachable",
        "unit_inactive",
        "unit_failed",
        "service_inactive",
    }:
        now = time.time()
        with _SYNCTHING_STABILIZE_LOCK:
            if (now - _LAST_SYNCTHING_STABILIZE_ATTEMPT) > 30.0:
                _LAST_SYNCTHING_STABILIZE_ATTEMPT = now
                st.set_running(True)
                status = st.get_runtime_status(timeout=3)
                service_active = bool(status.get("service_active", False))
                api_reachable = bool(status.get("api_reachable", False))
                reason = str(status.get("reason") or "unknown")
                unit_state = str(status.get("unit_state") or "unknown")
                unit_file_state = str(status.get("unit_file_state") or "unknown")
    return {
        "service_active": service_active,
        "api_reachable": api_reachable,
        "reason": reason,
        "unit_state": unit_state,
        "unit_file_state": unit_file_state,
    }


def collect_snapshot(*, target: str = "", include_media: bool = False, preferred_media_package: str = "") -> dict:
    result = {
        "battery": None,
        "network_type": None,
        "signal_strength": None,
        "media": None,
        "tailscale": False,
        "tailscale_local": False,
        "tailscale_ip": None,
        "tailscale_state": "unknown",
        "tailscale_mesh_reason": "",
        "tailscale_mesh_ready": False,
        "self_ip": None,
        "peers": [],
        "kde": True,
        "kde_enabled": True,
        "kde_reachable": False,
        "kde_status": "unknown",
        "syncthing": False,
        "syncthing_service_active": False,
        "syncthing_api_reachable": False,
        "syncthing_reason": "unknown",
        "syncthing_unit_state": "unknown",
        "syncthing_unit_file_state": "unknown",
        "wifi_enabled": None,
        "bt_enabled": None,
        "connectivity_status": {},
    }

    kc = KDEConnect()
    adb = ADBBridge(runtime_config.adb_target() or target)
    st = Syncthing()
    ts = Tailscale()

    try:
        battery = kc.get_battery()
        if not battery or int(battery.get("charge", -1)) < 0:
            level = adb.get_battery_level()
            if level >= 0:
                battery = {"charge": int(level), "is_charging": False, "source": "adb"}
        result["battery"] = battery
    except Exception:
        result["battery"] = None

    try:
        result["network_type"] = kc.get_network_type()
        result["signal_strength"] = kc.get_signal_strength()
    except Exception:
        result["network_type"] = None
        result["signal_strength"] = None

    try:
        net = str(result.get("network_type") or "").strip()
        sig = result.get("signal_strength")
        needs_net = (not net) or (net.lower() == "unknown")
        needs_sig = (sig is None) or (int(sig) < 0)
        if needs_net:
            rat = adb.get_mobile_network_label()
            hint = adb.get_active_network_hint()
            if rat:
                result["network_type"] = rat
            elif hint == "mobile":
                result["network_type"] = "Mobile"
            elif hint == "wifi":
                result["network_type"] = "WiFi"
        if needs_sig:
            fallback_sig = adb.get_signal_strength_level()
            if fallback_sig >= 0:
                result["signal_strength"] = fallback_sig
    except Exception:
        pass

    try:
        if settings.get("tailscale_force_off", False) and ts.is_connected():
            ts.down()
        snapshot = ts.get_mesh_snapshot(
            phone_name=runtime_config.device_name(),
            phone_ip=runtime_config.phone_tailscale_ip(),
        )
        _maybe_heal_phone_identity(snapshot)
        backend_state = str(snapshot.get("backend_state") or "").strip()
        result["tailscale_state"] = backend_state or "unknown"
        result["tailscale_local"] = bool(snapshot.get("local_connected", False))
        result["tailscale"] = bool(snapshot.get("mesh_ready", False))
        result["tailscale_mesh_ready"] = bool(snapshot.get("mesh_ready", False))
        result["tailscale_mesh_reason"] = str(snapshot.get("mesh_reason") or "")
        result["tailscale_ip"] = snapshot.get("self_ip")
        result["self_ip"] = snapshot.get("self_ip") if result["tailscale_local"] else None
        result["peers"] = list(snapshot.get("peers", []) or [])
    except Exception:
        result["tailscale"] = False
        result["tailscale_local"] = False
        result["tailscale_mesh_ready"] = False
        result["tailscale_mesh_reason"] = "tailscale status unavailable"
        result["tailscale_ip"] = None
        result["self_ip"] = None
        result["peers"] = []

    try:
        result["kde_enabled"] = bool(settings.get("kde_integration_enabled", True))
        result["kde"] = result["kde_enabled"]
        raw = kc.is_reachable() if result["kde_enabled"] else None
        result["kde_reachable"] = raw is True
        result["kde_status"] = (
            "disabled" if not result["kde_enabled"]
            else "reachable" if raw is True
            else "unreachable" if raw is False
            else "unknown"
        )
    except Exception:
        result["kde_enabled"] = bool(settings.get("kde_integration_enabled", True))
        result["kde"] = result["kde_enabled"]
        result["kde_reachable"] = False
        result["kde_status"] = "unknown"

    runtime = _collect_syncthing_runtime(st)
    syncthing_service_active = bool(runtime["service_active"])
    syncthing_api_reachable = bool(runtime["api_reachable"])
    syncthing_reason = str(runtime["reason"] or "unknown")
    syncthing_unit_state = str(runtime["unit_state"] or "unknown")
    syncthing_unit_file_state = str(runtime["unit_file_state"] or "unknown")
    syncthing_external_instance = bool((not syncthing_service_active) and syncthing_api_reachable)
    syncthing_effective_connected = bool(syncthing_api_reachable)
    result["syncthing"] = syncthing_service_active
    result["syncthing_service_active"] = syncthing_service_active
    result["syncthing_api_reachable"] = syncthing_api_reachable
    result["syncthing_reason"] = syncthing_reason
    result["syncthing_unit_state"] = syncthing_unit_state
    result["syncthing_unit_file_state"] = syncthing_unit_file_state

    try:
        result["wifi_enabled"] = adb.get_wifi_enabled()
    except Exception:
        result["wifi_enabled"] = None
    try:
        result["bt_enabled"] = adb.get_bluetooth_enabled()
    except Exception:
        result["bt_enabled"] = None

    if include_media:
        try:
            result["media"] = adb.get_now_playing(preferred_package=preferred_media_package)
        except Exception:
            result["media"] = None

    result["connectivity_status"] = {
        "tailscale": {
            "actual": bool(result["tailscale_local"]),
            "reachable": bool(result["tailscale_mesh_ready"]),
            "reason": result["tailscale_mesh_reason"] or f"state={result['tailscale_state']}",
        },
        "kde": {
            "actual": bool(result["kde_enabled"]),
            "reachable": bool(result["kde_reachable"]),
            "reason": result["kde_status"],
        },
        "syncthing": {
            "actual": syncthing_effective_connected,
            "reachable": syncthing_effective_connected,
            "reason": (
                f"api_reachable_external_instance (unit={syncthing_unit_state}, file={syncthing_unit_file_state})"
                if syncthing_external_instance
                else f"{syncthing_reason} (unit={syncthing_unit_state}, file={syncthing_unit_file_state})"
            ),
        },
        "wifi": {
            "actual": bool(result["wifi_enabled"]) if result["wifi_enabled"] is not None else False,
            "reachable": result["wifi_enabled"] is not None,
            "reason": "ok" if result["wifi_enabled"] is not None else "unknown",
        },
        "bluetooth": {
            "actual": bool(result["bt_enabled"]) if result["bt_enabled"] is not None else False,
            "reachable": result["bt_enabled"] is not None,
            "reason": "ok" if result["bt_enabled"] is not None else "unknown",
        },
    }
    return result


def collect_sync_snapshot() -> dict:
    st = Syncthing()
    runtime = _collect_syncthing_runtime(st)
    service_active = bool(runtime["service_active"])
    api_reachable = bool(runtime["api_reachable"])
    effective_connected = bool((service_active and api_reachable) or ((not service_active) and api_reachable))
    payload = {
        "running": effective_connected,
        "service_active": service_active,
        "api_reachable": api_reachable,
        "unit_file_state": str(runtime["unit_file_state"] or "unknown"),
        "reason": str(runtime["reason"] or "unknown"),
        "folders": [],
        "rates": {},
    }
    if effective_connected:
        payload["folders"] = st.get_folders()
        payload["rates"] = st.get_transfer_rates()
    return payload
