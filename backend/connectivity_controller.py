"""Shared consequential connectivity toggles with state verification."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time

import backend.settings_store as settings
from backend.adb_bridge import ADBBridge
from backend.bluetooth_manager import BluetoothManager
from backend.kdeconnect import KDEConnect
from backend.state import state
from backend.syncthing import Syncthing
from backend.tailscale import Tailscale


_LOCKS = {
    "wifi": threading.Lock(),
    "bluetooth": threading.Lock(),
    "tailscale": threading.Lock(),
    "kde": threading.Lock(),
    "syncthing": threading.Lock(),
}


def _set_busy(op: str, busy: bool) -> None:
    def _update(current):
        current[op] = bool(busy)
        return current

    state.update("connectivity_ops_busy", _update, default={})


def _try_begin(op: str) -> threading.Lock | None:
    lock = _LOCKS.get(op)
    if lock is None:
        return None
    if not lock.acquire(blocking=False):
        return None
    _set_busy(op, True)
    return lock


def _end(op: str, lock: threading.Lock | None) -> None:
    _set_busy(op, False)
    if lock is None:
        return
    try:
        lock.release()
    except RuntimeError:
        pass


def _wait_for_bool(getter, target: bool, timeout_s: float = 3.5, step_s: float = 0.3):
    end = time.time() + timeout_s
    last = None
    while time.time() < end:
        last = getter()
        if last is not None and bool(last) == bool(target):
            return True, bool(last)
        time.sleep(step_s)
    if last is None:
        return False, None
    return bool(last) == bool(target), bool(last)


def _publish_syncthing_runtime_status(status: dict, op: str) -> None:
    payload = {
        "service_active": bool((status or {}).get("service_active", False)),
        "api_reachable": bool((status or {}).get("api_reachable", False)),
        "unit_state": str((status or {}).get("unit_state") or "unknown"),
        "unit_file_state": str((status or {}).get("unit_file_state") or "unknown"),
        "reason": str((status or {}).get("reason") or "unknown"),
        "op": str(op or "unknown"),
        "updated_at": int(time.time() * 1000),
    }
    state.set("syncthing_runtime_status", payload)


def set_wifi(enabled: bool, target: str | None = None):
    lock = _try_begin("wifi")
    if lock is None:
        return False, "Wi-Fi operation already in progress", None
    try:
        adb = ADBBridge(target)
        desired = bool(enabled)
        cmd_ok = bool(adb.set_wifi(desired))
        actual_ok, actual = _wait_for_bool(adb.get_wifi_enabled, desired, timeout_s=4.0, step_s=0.35)
        if not actual_ok:
            return False, "Wi-Fi state not confirmed", actual
        return cmd_ok, ("Wi-Fi enabled" if desired else "Wi-Fi disabled"), actual
    finally:
        _end("wifi", lock)


def set_bluetooth(enabled: bool, target: str | None = None):
    lock = _try_begin("bluetooth")
    if lock is None:
        return False, "Bluetooth operation already in progress", None
    try:
        adb = ADBBridge(target)
        desired = bool(enabled)
        cmd_ok = bool(adb.set_bluetooth(desired))
        actual_ok, actual = _wait_for_bool(adb.get_bluetooth_enabled, desired, timeout_s=4.0, step_s=0.35)
        if not actual_ok:
            return False, "Bluetooth state not confirmed", actual
        if desired and settings.get("auto_bt_connect", True):
            hints = [
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            BluetoothManager().auto_connect_phone(
                hints,
                call_ready_only=bool(settings.get("bt_call_ready_mode", False)),
            )
        return cmd_ok, ("Bluetooth enabled" if desired else "Bluetooth disabled"), actual
    finally:
        _end("bluetooth", lock)


def set_tailscale(enabled: bool):
    lock = _try_begin("tailscale")
    if lock is None:
        return False, "Tailscale operation already in progress", None
    try:
        ts = Tailscale()
        desired = bool(enabled)
        settings.set("tailscale_force_off", not desired)
        cmd_ok = bool(ts.set_enabled(desired))
        if not cmd_ok:
            return False, ts.last_error() or "Tailscale command failed", ts.is_connected()
        actual_ok, actual = _wait_for_bool(ts.is_connected, desired, timeout_s=4.5, step_s=0.4)
        if (not desired) and bool(actual):
            # Some setups auto-reconnect quickly; enforce one more explicit down.
            ts.down()
            actual_ok, actual = _wait_for_bool(ts.is_connected, False, timeout_s=5.0, step_s=0.5)
        if not actual_ok:
            return False, "Tailscale did not reach requested state", actual
        return True, ("Tailscale connected" if desired else "Tailscale disconnected"), actual
    finally:
        _end("tailscale", lock)


def _systemctl_user(action: str, unit: str):
    if not shutil.which("systemctl"):
        return False, "systemctl unavailable"
    try:
        proc = subprocess.run(
            ["systemctl", "--user", action, unit],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return False, str(exc)
    out = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode == 0, out


def _systemctl_user_unit_exists(unit: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "status", unit],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return False
    txt = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    return "could not be found" not in txt


def _systemctl_user_active(unit: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=4,
        )
    except Exception:
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == "active"


def is_user_service_active(unit: str) -> bool:
    return _systemctl_user_active(str(unit or ""))


def _kde_daemon_running() -> bool:
    if not shutil.which("pgrep"):
        return False
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "kdeconnectd"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def set_kde(enabled: bool, *, window=None):
    lock = _try_begin("kde")
    if lock is None:
        return False, "KDE Connect operation already in progress", None
    try:
        desired = bool(enabled)
        has_unit = _systemctl_user_unit_exists("kdeconnectd.service")
        if desired:
            if has_unit:
                ok, msg = _systemctl_user("start", "kdeconnectd.service")
                if not ok:
                    return False, msg or "Failed to start kdeconnectd.service", False
            elif not _kde_daemon_running():
                try:
                    subprocess.Popen(
                        ["kdeconnectd"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except Exception as exc:
                    return False, f"Failed to launch kdeconnectd: {exc}", False
            bridge_ok = True
            if window is not None and hasattr(window, "set_kde_integration"):
                bridge_ok = bool(window.set_kde_integration(True))
            else:
                settings.set("kde_integration_enabled", True)
            active = _systemctl_user_active("kdeconnectd.service") if has_unit else _kde_daemon_running()
            reachable = current_kde_reachable()
            actual = bool(settings.get("kde_integration_enabled", True) and (active or reachable))
            if not (bridge_ok and actual):
                return False, "KDE integration did not reach requested state", actual
            return True, "KDE Connect enabled", actual

        bridge_ok = True
        if window is not None and hasattr(window, "set_kde_integration"):
            bridge_ok = bool(window.set_kde_integration(False))
        else:
            settings.set("kde_integration_enabled", False)
        if has_unit:
            ok, msg = _systemctl_user("stop", "kdeconnectd.service")
            if not ok:
                return False, msg or "Failed to stop kdeconnectd.service", True
            active = _systemctl_user_active("kdeconnectd.service")
        else:
            if shutil.which("pkill"):
                try:
                    subprocess.run(["pkill", "-x", "kdeconnectd"], capture_output=True, text=True, timeout=3)
                except Exception:
                    pass
            active = _kde_daemon_running()
        actual = bool(active or settings.get("kde_integration_enabled", True))
        if bridge_ok and not actual:
            return True, "KDE Connect disabled", False
        return False, "KDE integration did not reach requested state", actual
    finally:
        _end("kde", lock)


def set_syncthing(enabled: bool):
    lock = _try_begin("syncthing")
    if lock is None:
        return False, "Syncthing operation already in progress", None
    try:
        desired = bool(enabled)
        st = Syncthing()
        if desired:
            cmd_ok = bool(st.set_running(True))
            status = st.get_runtime_status(timeout=3)
            _publish_syncthing_runtime_status(status, "enable")
            api_up = bool(status.get("api_reachable", False))
            if not api_up:
                return (
                    bool(cmd_ok),
                    f"Syncthing API unreachable ({status.get('api_reason', 'unknown')})",
                    False,
                )
            return (
                bool(cmd_ok),
                "Syncthing reachable",
                True,
            )

        # Disable path: stop managed service first.
        cmd_ok = bool(st.set_running(False))
        status = st.get_runtime_status(timeout=3)
        api_up = bool(status.get("api_reachable", False))

        # If API is still reachable, this is likely an external instance.
        # Request graceful shutdown through the REST API.
        shutdown_ok = True
        if api_up:
            shutdown_ok = bool(st.shutdown_api(timeout=6))
            _wait_for_bool(lambda: st.ping_status(timeout=2)[0], False, timeout_s=6.0, step_s=0.4)
            status = st.get_runtime_status(timeout=3)
            api_up = bool(status.get("api_reachable", False))

        _publish_syncthing_runtime_status(status, "disable")

        effective_connected = bool(api_up)
        if effective_connected:
            return (
                False,
                "Syncthing API still reachable (external instance did not stop)",
                True,
            )
        return (
            bool(cmd_ok and shutdown_ok),
            "Syncthing stopped",
            False,
        )
    finally:
        _end("syncthing", lock)


def current_kde_reachable() -> bool:
    if not settings.get("kde_integration_enabled", True):
        return False
    try:
        return bool(KDEConnect().is_reachable())
    except Exception:
        return False
