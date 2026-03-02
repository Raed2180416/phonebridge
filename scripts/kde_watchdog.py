#!/usr/bin/env python3
"""KDE Connect watchdog with Tailscale+ADB gated phone wake."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable


LOG_PREFIX = "[kde-watchdog]"


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


@dataclass
class Config:
    device_id: str
    phone_tailscale_ip: str
    adb_target: str
    kde_app_package: str
    fail_threshold: int
    wake_cooldown_sec: int
    state_dir: Path


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}")


def run_cmd(cmd: list[str], timeout: float = 8.0) -> CommandResult:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return CommandResult(False, "", str(exc), 127)
    return CommandResult(proc.returncode == 0, proc.stdout or "", proc.stderr or "", proc.returncode)


def _normalize_adb_serial(serial: str) -> str:
    return re.sub(r"\s+", "", str(serial or "").strip())


def ensure_tools() -> tuple[bool, str]:
    needed = ["kdeconnect-cli", "tailscale", "adb"]
    missing = [tool for tool in needed if shutil.which(tool) is None]
    if missing:
        return False, f"Missing required tools: {', '.join(missing)}"
    return True, ""


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        row = line.strip()
        if not row or row.startswith("#") or "=" not in row:
            continue
        key, val = row.split("=", 1)
        values[key.strip()] = val.strip()
    return values


def _env_or_file(key: str, env_file: dict[str, str], default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    return str(env_file.get(key, default)).strip()


def load_config() -> Config:
    env_path = Path.home() / ".config" / "phonebridge" / "kde-watchdog.env"
    env_file = _parse_env_file(env_path)

    device_id = _env_or_file("DEVICE_ID", env_file)
    phone_ip = _env_or_file("PHONE_TAILSCALE_IP", env_file)
    adb_target = _env_or_file("ADB_TARGET", env_file)
    kde_app_package = _env_or_file("KDE_APP_PACKAGE", env_file, "org.kde.kdeconnect_tp")
    fail_threshold_raw = _env_or_file("FAIL_THRESHOLD", env_file, "2")
    cooldown_raw = _env_or_file("WAKE_COOLDOWN_SEC", env_file, "600")
    state_dir_raw = _env_or_file(
        "STATE_DIR",
        env_file,
        str(Path.home() / ".cache" / "phonebridge" / "kde-watchdog"),
    )

    missing = [name for name, value in (
        ("DEVICE_ID", device_id),
        ("PHONE_TAILSCALE_IP", phone_ip),
        ("ADB_TARGET", adb_target),
    ) if not value]
    if missing:
        raise ValueError(f"Missing required config values: {', '.join(missing)}")

    try:
        fail_threshold = max(1, int(fail_threshold_raw))
    except Exception as exc:
        raise ValueError(f"Invalid FAIL_THRESHOLD: {fail_threshold_raw}") from exc
    try:
        wake_cooldown = max(0, int(cooldown_raw))
    except Exception as exc:
        raise ValueError(f"Invalid WAKE_COOLDOWN_SEC: {cooldown_raw}") from exc

    return Config(
        device_id=device_id,
        phone_tailscale_ip=phone_ip,
        adb_target=adb_target,
        kde_app_package=kde_app_package,
        fail_threshold=fail_threshold,
        wake_cooldown_sec=wake_cooldown,
        state_dir=Path(state_dir_raw).expanduser(),
    )


def read_int(path: Path, default: int = 0) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return int(default)


def write_int(path: Path, value: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(value)), encoding="utf-8")


def kde_available_ids() -> set[str]:
    res = run_cmd(["kdeconnect-cli", "--list-available", "--id-only"], timeout=8)
    if not res.ok:
        err = (res.stderr or res.stdout).strip()
        log(f"kdeconnect availability check failed rc={res.returncode}: {err}")
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def is_kde_reachable(device_id: str) -> bool:
    return device_id in kde_available_ids()


def kde_refresh() -> bool:
    res = run_cmd(["kdeconnect-cli", "--refresh"], timeout=8)
    if not res.ok:
        err = (res.stderr or res.stdout).strip()
        log(f"kdeconnect refresh failed rc={res.returncode}: {err}")
    return res.ok


def tailscale_status() -> dict | None:
    res = run_cmd(["tailscale", "status", "--json"], timeout=8)
    if not res.ok:
        err = (res.stderr or res.stdout).strip()
        log(f"tailscale status failed rc={res.returncode}: {err}")
        return None
    try:
        return json.loads(res.stdout or "{}")
    except Exception as exc:
        log(f"tailscale status parse failed: {exc}")
        return None


def tailscale_local_online(status: dict | None) -> bool:
    if not isinstance(status, dict):
        return False
    if str(status.get("BackendState") or "").strip() != "Running":
        return False
    return bool((status.get("Self") or {}).get("Online", False))


def tailscale_phone_online(status: dict | None, phone_ip: str) -> bool:
    if not isinstance(status, dict):
        return False
    target_ip = str(phone_ip or "").strip()
    if not target_ip:
        return False
    peers = (status.get("Peer") or {}).values()
    for peer in peers:
        row = peer or {}
        ips = list(row.get("TailscaleIPs") or [])
        if target_ip in ips:
            return bool(row.get("Online", False))
    return False


def ensure_adb_connected(target: str) -> bool:
    normalized = _normalize_adb_serial(target)
    run_cmd(["adb", "connect", normalized], timeout=7)
    res = run_cmd(["adb", "devices"], timeout=6)
    if not res.ok:
        err = (res.stderr or res.stdout).strip()
        log(f"adb devices failed rc={res.returncode}: {err}")
        return False
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = _normalize_adb_serial(parts[0])
        state = parts[1].strip().lower()
        if serial == normalized and state == "device":
            return True
    return False


def wake_phone_kde_app(target: str, package: str) -> bool:
    cmd = [
        "adb",
        "-s",
        _normalize_adb_serial(target),
        "shell",
        "monkey",
        "-p",
        str(package).strip(),
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    ]
    res = run_cmd(cmd, timeout=12)
    if not res.ok:
        err = (res.stderr or res.stdout).strip()
        log(f"phone wake command failed rc={res.returncode}: {err}")
    return res.ok


def run_watchdog(
    config: Config,
    *,
    time_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    fail_file = config.state_dir / "fail_count"
    wake_file = config.state_dir / "last_wake_epoch"

    reachable = is_kde_reachable(config.device_id)
    if reachable:
        if read_int(fail_file, 0) != 0:
            write_int(fail_file, 0)
        log("healthy: kde reachable")
        return 0

    fail_count = read_int(fail_file, 0) + 1
    write_int(fail_file, fail_count)
    log(f"kde unreachable (fail_count={fail_count})")
    if fail_count < config.fail_threshold:
        log("debounce threshold not reached; skipping recovery")
        return 0

    log("attempting kde refresh")
    kde_refresh()
    sleep_fn(2.0)
    if is_kde_reachable(config.device_id):
        write_int(fail_file, 0)
        log("recovered after kde refresh")
        return 0

    ts_status = tailscale_status()
    local_online = tailscale_local_online(ts_status)
    phone_online = tailscale_phone_online(ts_status, config.phone_tailscale_ip)
    adb_online = ensure_adb_connected(config.adb_target)

    if not local_online:
        log("gate blocked: local tailscale offline")
        return 0
    if not phone_online:
        log("gate blocked: phone peer offline in tailscale")
        return 0
    if not adb_online:
        log("gate blocked: adb target not connected")
        return 0

    now = int(time_fn())
    last_wake = read_int(wake_file, 0)
    elapsed = now - last_wake
    if elapsed < config.wake_cooldown_sec:
        log(f"cooldown active: {config.wake_cooldown_sec - elapsed}s remaining")
        return 0

    log("all gates true; waking KDE Connect app on phone")
    if not wake_phone_kde_app(config.adb_target, config.kde_app_package):
        return 0
    write_int(wake_file, now)

    sleep_fn(3.0)
    kde_refresh()
    if is_kde_reachable(config.device_id):
        write_int(fail_file, 0)
        log("recovered after phone wake")
    else:
        log("still unreachable after phone wake")
    return 0


def main() -> int:
    ok, err = ensure_tools()
    if not ok:
        log(err)
        return 3
    try:
        config = load_config()
    except Exception as exc:
        log(str(exc))
        return 2
    try:
        return run_watchdog(config)
    except Exception as exc:
        log(f"internal error: {exc}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
