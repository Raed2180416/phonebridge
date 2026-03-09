"""Global phone-audio routing controller (single scrcpy --no-video owner)."""
from __future__ import annotations

import subprocess
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass

from backend.adb_bridge import ADBBridge
import backend.settings_store as settings
from backend.state import state

log = logging.getLogger(__name__)

_audio_proc = None
_audio_mode = None
_sources = {
    "ui_global_toggle": bool(settings.get("audio_redirect", False)),
    "call_pc_active": False,
}

# ── BT call mic-path watchdog ─────────────────────────────────────────────────
# Runs while call_audio_active=True.  If the mic path drops mid-call, it
# re-triggers sync_result() to re-establish or surface the failure.
_WATCHDOG_INTERVAL_S = 5.0
_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()


def _start_call_route_watchdog() -> None:
    """Start BT mic-path health watchdog.  No-op if already running."""
    global _watchdog_thread
    _watchdog_stop.clear()
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    t = threading.Thread(target=_call_route_watchdog_loop, name="pb-bt-watchdog", daemon=True)
    _watchdog_thread = t
    t.start()
    log.debug("BT call-route watchdog started")


def _stop_call_route_watchdog() -> None:
    """Signal watchdog to stop; does not block."""
    _watchdog_stop.set()
    log.debug("BT call-route watchdog stop requested")


def _call_route_watchdog_loop() -> None:
    """Background loop: if mic path drops while call active, re-trigger sync."""
    while not _watchdog_stop.wait(_WATCHDOG_INTERVAL_S):
        if not bool(state.get("call_audio_active", False)):
            # Route no longer active — exit watchdog cleanly.
            log.debug("BT watchdog: call_audio_active=False, exiting")
            return
        if not _bt_call_mic_path_active():
            log.warning("BT watchdog: call mic path dropped mid-call; attempting re-sync")
            try:
                result = sync_result(call_retry_ms=4000, retry_step_ms=300, suspend_ui_global=True)
                if result.ok:
                    log.info("BT watchdog: mic path restored (backend=%s)", result.backend)
                else:
                    log.warning("BT watchdog: re-sync failed (status=%s, reason=%s)",
                                result.status, result.reason)
            except Exception:
                log.exception("BT watchdog: re-sync raised exception")
    log.debug("BT call-route watchdog stopped")


@dataclass
class RouteSyncResult:
    ok: bool
    status: str
    mode: str | None
    backend: str
    reason: str


class _ExternalBTRouteProc:
    """Pseudo-process for active external Bluetooth media routing."""
    def __init__(self, active_check=None):
        self._active_check = active_check or _bt_media_route_active

    def poll(self):
        return None if self._active_check() else 0

    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None

# Initialize state
state.set("audio_redirect_enabled", _sources["ui_global_toggle"])
state.set("call_audio_active", _sources["call_pc_active"])
state.set("call_route_status", "phone")
state.set("call_route_reason", "")
state.set("call_route_backend", "none")


def _set_call_route_state(status: str, reason: str = "", backend: str = "none") -> None:
    state.set("call_route_status", status)
    state.set("call_route_reason", reason)
    state.set("call_route_backend", backend)


def _restore_call_audio_session_if_needed() -> None:
    try:
        from backend import call_audio

        if call_audio.session_active():
            call_audio.end_session_restore()
    except Exception:
        log.exception("Failed restoring call-audio session snapshot")


def _is_running() -> bool:
    global _audio_proc
    if _audio_proc is None:
        return False
    if _audio_proc.poll() is None:
        return True
    _audio_proc = None
    return False


def _bt_media_route_active() -> bool:
    # Media route detection must only look at output paths.
    # `bluez_input` is call mic path and should not suppress global media routing.
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            out = (r.stdout or "").lower()
            if "bluez_output." in out:
                return True
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            out = (r.stdout or "").lower()
            if "bluez_output." in out:
                return True
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["wpctl", "status"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            for raw in (r.stdout or "").splitlines():
                line = raw.strip().lower()
                if "bluez_output." in line:
                    return True
    except Exception:
        pass
    return False


def _bt_call_profile_present() -> bool:
    profile_active = False
    try:
        r = subprocess.run(
            ["wpctl", "status"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode != 0:
            return False
        dev_ids: list[str] = []
        for raw in (r.stdout or "").splitlines():
            line = raw.strip().lower()
            if "[bluez5]" not in line:
                continue
            # Example: "80. Nothing Phone (3a) Pro [bluez5]"
            parts = line.split(".", 1)
            if not parts:
                continue
            dev_id = parts[0].strip(" *│├└")
            if dev_id.isdigit():
                dev_ids.append(dev_id)
        for dev_id in dev_ids:
            ri = subprocess.run(
                ["wpctl", "inspect", dev_id],
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            if ri.returncode != 0:
                continue
            txt = (ri.stdout or "").lower()
            if "bluez5.profile = \"off\"" in txt and "device.profile = \"audio-gateway\"" not in txt:
                continue
            if any(
                k in txt
                for k in (
                    "handsfree",
                    "headset",
                    "hfp",
                    "hsp",
                    "audio-gateway",
                    "audio_gateway",
                    "device.profile = \"audio-gateway\"",
                )
            ):
                profile_active = True
                break
    except Exception:
        profile_active = False
    if profile_active:
        return True
    try:
        r = subprocess.run(
            ["pactl", "list", "cards"],
            capture_output=True,
            text=True,
            timeout=0.7,
        )
        if r.returncode == 0:
            low = (r.stdout or "").lower()
            if "bluez_card." in low and any(
                token in low
                for token in (
                    "active profile: audio-gateway",
                    "active profile: headset-head-unit",
                    "active profile: handsfree_head_unit",
                    "active profile: hfp_hf",
                    "active profile: hsp_hs",
                    "audio-gateway",
                    "handsfree",
                    "headset",
                    "hfp",
                    "hsp",
                )
            ):
                return True
    except Exception:
        log.debug("pactl card fallback failed while probing BT call profile", exc_info=True)
    return False


def _bt_call_mic_path_active() -> bool:
    def _wpctl_sources_has_bt_call_source(status_text: str) -> bool:
        in_sources = False
        for raw in (status_text or "").splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            header = stripped.strip("│├└─* ").lower()
            if header == "sources:":
                in_sources = True
                continue
            if header.endswith(":") and header != "sources:":
                in_sources = False
                continue
            if not in_sources:
                continue
            line = stripped.lower()
            if any(
                token in line
                for token in ("bluez_input.", "handsfree", "headset", "hfp", "hsp", "audio-gateway", "audio_gateway")
            ):
                return True
        return False

    # Call mode requires a Bluetooth input node (laptop mic path).
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            out = (r.stdout or "").lower()
            if any(k in out for k in ("bluez_input.", "handsfree", "headset")):
                return True
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["pactl", "list", "sources"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            out = (r.stdout or "").lower()
            if "device.api = \"bluez5\"" in out:
                return True
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["wpctl", "status"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            if _wpctl_sources_has_bt_call_source(r.stdout or ""):
                return True
    except Exception:
        pass
    return False


def _bt_call_profile_active() -> bool:
    """Backward-compatible alias for the old name."""
    return _bt_call_mic_path_active()


def _boost_call_mic_gain() -> None:
    """Best-effort call mic gain boost for active BT call routing."""
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sources"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
        if r.returncode == 0:
            for raw in (r.stdout or "").splitlines():
                row = raw.strip().lower()
                if "bluez_input." not in row:
                    continue
                parts = raw.split("\t")
                if len(parts) < 2:
                    continue
                src = parts[1].strip()
                if not src:
                    continue
                subprocess.run(
                    ["pactl", "set-source-volume", src, "140%"],
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                )
                return
    except Exception:
        pass
    try:
        subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", "1.40"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except Exception:
        pass


def _enforce_call_ready_bt_mode() -> None:
    if not bool(settings.get("bt_call_ready_mode", False)):
        return
    try:
        from backend.bluetooth_manager import BluetoothManager

        mgr = BluetoothManager()
        if not mgr.available():
            return
        hints = [
            settings.get("device_name", ""),
            "nothing",
            "phone",
            "a059",
        ]
        changed, msg = mgr.enforce_call_ready_mode(hints)
        if changed:
            log.info("Enforced Bluetooth call-ready mode: %s", msg)
    except Exception:
        log.exception("Failed enforcing Bluetooth call-ready mode")


def _scrcpy_audio_pids() -> list[int]:
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    pids: list[int] = []
    for raw in (r.stdout or "").splitlines():
        row = raw.strip()
        if not row:
            continue
        parts = row.split(maxsplit=1)
        if len(parts) < 2:
            continue
        pid_txt, cmd = parts[0], parts[1]
        if "scrcpy" not in cmd:
            continue
        if "--no-video" not in cmd or "--no-window" not in cmd:
            continue
        if "--audio-source=output" not in cmd and "--audio-source=voice-call" not in cmd:
            continue
        try:
            pids.append(int(pid_txt))
        except Exception:
            continue
    return pids


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        return
    for _ in range(6):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except Exception:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _cleanup_orphan_audio_procs(exclude: set[int] | None = None) -> int:
    excluded = set(exclude or set())
    killed = 0
    for pid in _scrcpy_audio_pids():
        if pid in excluded:
            continue
        _kill_pid(pid)
        killed += 1
    if killed:
        log.info("Stopped %s orphan scrcpy audio process(es)", killed)
    return killed


def _stop_proc() -> bool:
    global _audio_proc, _audio_mode
    tracked_pid = None
    if _audio_proc is not None:
        try:
            tracked_pid = int(_audio_proc.pid)
        except Exception:
            tracked_pid = None
    if _audio_proc is None:
        _audio_mode = None
        _cleanup_orphan_audio_procs()
        return True
    log.info("Stopping audio routing process (mode=%s)", _audio_mode)
    try:
        _audio_proc.terminate()
        _audio_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            _audio_proc.kill()
        except Exception:
            pass
    except Exception:
        pass
    _audio_proc = None
    _audio_mode = None
    _cleanup_orphan_audio_procs(exclude={tracked_pid} if tracked_pid else None)
    return True


def _start_proc(mode: str, adb: ADBBridge | None = None) -> bool:
    global _audio_proc, _audio_mode
    _cleanup_orphan_audio_procs()
    if mode == "audio_output":
        # Keep media path deterministic: global media routing always uses scrcpy.
        # Bluetooth path ownership is reserved for active calls only.
        bridge = adb or ADBBridge()
        log.info("Starting scrcpy media audio route (mode=%s)", mode)
        proc = bridge.launch_scrcpy(mode)
        if not proc:
            log.error("Failed to launch audio routing process (mode=%s)", mode)
            _audio_proc = None
            _audio_mode = None
            return False
        _audio_proc = proc
        _audio_mode = mode
        return True
    if mode == "audio":
        if _bt_call_mic_path_active():
            log.info("Bluetooth call mic path active; skipping scrcpy call-audio route")
            _audio_proc = _ExternalBTRouteProc(active_check=_bt_call_mic_path_active)
            _audio_mode = mode
            return True
        if not _bt_call_profile_present():
            log.warning("Cannot start call-audio route: Bluetooth call profile unavailable")
            _audio_proc = None
            _audio_mode = None
            return False
        log.info("Bluetooth call profile detected; waiting for mic path before activating call route")
        _audio_proc = None
        _audio_mode = None
        return False
    bridge = adb or ADBBridge()
    log.info("Starting audio routing process (mode=%s)", mode)
    proc = bridge.launch_scrcpy(mode)
    if not proc:
        log.error("Failed to launch audio routing process (mode=%s)", mode)
        _audio_proc = None
        _audio_mode = None
        return False
    _audio_proc = proc
    _audio_mode = mode
    return True


def is_running() -> bool:
    return _is_running()


def active_backend() -> str:
    if not _is_running():
        return "none"
    if isinstance(_audio_proc, _ExternalBTRouteProc):
        return "external_bt"
    return "scrcpy"


def current_sources() -> dict[str, bool]:
    return dict(_sources)


def clear_all() -> None:
    for key in list(_sources.keys()):
        _sources[key] = False
    settings.set("audio_redirect", False)
    state.set("audio_redirect_enabled", False)
    state.set("call_audio_active", False)
    _set_call_route_state("phone", "Call audio on phone", "none")
    _stop_proc()
    _restore_call_audio_session_if_needed()
    _enforce_call_ready_bt_mode()


def set_source(source_id: str, enabled: bool) -> None:
    sid = str(source_id or "").strip()
    if not sid:
        return
    val = bool(enabled)
    if _sources.get(sid) == val:
        return
    _sources[sid] = val
    if sid == "ui_global_toggle":
        settings.set("audio_redirect", val)
        state.set("audio_redirect_enabled", val)
    elif sid == "call_pc_active":
        if not val:
            state.set("call_audio_active", False)
            _set_call_route_state("phone", "Call audio on phone", "none")


def is_effective_enabled(*, suspend_ui_global: bool = False) -> bool:
    if bool(_sources.get("call_pc_active", False)):
        return True
    if suspend_ui_global:
        return False
    return bool(_sources.get("ui_global_toggle", False))


def _desired_mode(*, suspend_ui_global: bool = False) -> str | None:
    if bool(_sources.get("call_pc_active", False)):
        return "audio"
    if not suspend_ui_global and bool(_sources.get("ui_global_toggle", False)):
        return "audio_output"
    return None


def _wait_for_bt_call_mic_path(call_retry_ms: int, retry_step_ms: int) -> bool:
    if _bt_call_mic_path_active():
        return True
    timeout_ms = max(0, int(call_retry_ms or 0))
    if timeout_ms <= 0:
        return False
    step_ms = max(50, int(retry_step_ms or 300))
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        time.sleep(step_ms / 1000.0)
        if _bt_call_mic_path_active():
            return True
    return _bt_call_mic_path_active()


def _call_route_active_result(reason: str = "Bluetooth call mic path is active") -> RouteSyncResult:
    # Guard: if call_pc_active was cleared while sync_result() was running on a
    # background/QThread, do NOT write pc_active state.  Without this, the
    # background thread's state.set() calls race against set_route_phone()'s
    # corrections and can overwrite _data after the main thread already fixed it,
    # causing the tile UI to revert to "laptop active" even though phone was
    # selected.
    if not bool(_sources.get("call_pc_active", False)):
        log.debug("_call_route_active_result: call_pc_active cancelled – discarding stale result")
        _stop_call_route_watchdog()
        return RouteSyncResult(
            ok=False,
            status="cancelled",
            mode="audio",
            backend="none",
            reason="Route request was cancelled",
        )
    try:
        from backend import call_audio

        call_audio.apply_saved_settings()
    except Exception:
        log.exception("Failed applying saved call-audio settings")
    _boost_call_mic_gain()
    backend = active_backend()
    state.set("call_audio_active", True)
    _set_call_route_state("pc_active", "Audio on laptop/PC", backend)
    _start_call_route_watchdog()  # monitor mic path while call is active
    return RouteSyncResult(
        ok=True,
        status="active",
        mode="audio",
        backend=backend,
        reason=reason,
    )


def _call_route_pending_result(reason: str = "Waiting for Bluetooth call mic path") -> RouteSyncResult:
    state.set("call_audio_active", False)
    _set_call_route_state("pending_pc", reason, "none")
    return RouteSyncResult(
        ok=False,
        status="pending",
        mode="audio",
        backend="none",
        reason=reason,
    )


def _call_route_failed_result(reason: str) -> RouteSyncResult:
    state.set("call_audio_active", False)
    _stop_call_route_watchdog()
    _set_call_route_state("pc_failed", reason, "none")
    return RouteSyncResult(
        ok=False,
        status="failed",
        mode="audio",
        backend="none",
        reason=reason,
    )


def sync_result(
    adb: ADBBridge | None = None,
    *,
    suspend_ui_global: bool = False,
    call_retry_ms: int = 0,
    retry_step_ms: int = 300,
) -> RouteSyncResult:
    want_mode = _desired_mode(suspend_ui_global=suspend_ui_global)
    running = _is_running()
    end_call_session = (want_mode != "audio") and (not bool(_sources.get("call_pc_active", False)))

    if want_mode is None:
        if running:
            _stop_proc()
            _enforce_call_ready_bt_mode()
            if end_call_session:
                _restore_call_audio_session_if_needed()
            if not bool(_sources.get("call_pc_active", False)):
                _stop_call_route_watchdog()
                state.set("call_audio_active", False)
            return RouteSyncResult(
                ok=True,
                status="stopped",
                mode=None,
                backend="none",
                reason="Audio route stopped",
            )
        else:
            _cleanup_orphan_audio_procs()
            if end_call_session:
                _restore_call_audio_session_if_needed()
            if not bool(_sources.get("call_pc_active", False)):
                _stop_call_route_watchdog()
                state.set("call_audio_active", False)
            return RouteSyncResult(
                ok=True,
                status="noop",
                mode=None,
                backend="none",
                reason="No active audio route",
            )

    if want_mode == "audio":
        if running and _audio_mode != want_mode:
            _stop_proc()
            running = False
        if running and _audio_mode == want_mode:
            if _bt_call_mic_path_active():
                return _call_route_active_result()
            if _wait_for_bt_call_mic_path(call_retry_ms, retry_step_ms):
                return _call_route_active_result()
            if int(call_retry_ms or 0) > 0:
                return _call_route_failed_result("Bluetooth call mic path not detected in time")
            return _call_route_pending_result()

        # Some devices expose the BT call mic path even when profile probes are
        # flaky. Prefer observed mic-path readiness over profile heuristics.
        if _wait_for_bt_call_mic_path(call_retry_ms, retry_step_ms):
            started = _start_proc("audio", adb=adb)
            if started and _bt_call_mic_path_active():
                return _call_route_active_result()
            return _call_route_pending_result()

        if not _bt_call_profile_present():
            return _call_route_failed_result("Bluetooth call profile unavailable")

        if int(call_retry_ms or 0) > 0:
            return _call_route_failed_result("Bluetooth call mic path not detected in time")
        return _call_route_pending_result()

    if running and _audio_mode == want_mode:
        if end_call_session:
            _restore_call_audio_session_if_needed()
        return RouteSyncResult(
            ok=True,
            status="active",
            mode=want_mode,
            backend=active_backend(),
            reason="Audio route already active",
        )
    if running and _audio_mode != want_mode:
        _stop_proc()
    if end_call_session:
        _restore_call_audio_session_if_needed()
    started = _start_proc(want_mode, adb=adb)
    return RouteSyncResult(
        ok=bool(started),
        status="active" if started else "failed",
        mode=want_mode,
        backend=active_backend() if started else "none",
        reason="Audio route active" if started else "Failed to start audio route",
    )


def sync(adb: ADBBridge | None = None, *, suspend_ui_global: bool = False) -> bool:
    return sync_result(adb=adb, suspend_ui_global=suspend_ui_global).ok


def start(adb: ADBBridge | None = None) -> bool:
    set_source("ui_global_toggle", True)
    return sync(adb=adb)


def stop() -> bool:
    _stop_call_route_watchdog()
    ok = _stop_proc()
    _restore_call_audio_session_if_needed()
    _enforce_call_ready_bt_mode()
    return ok


def set_enabled(enabled: bool, adb: ADBBridge | None = None) -> bool:
    set_source("ui_global_toggle", bool(enabled))
    return sync(adb=adb)
