"""Shared helpers for PhoneBridge hardware acceptance harnesses."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".cache" / "phonebridge" / "phonebridge.log"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def socket_path() -> Path:
    uid = os.getuid()
    for candidate in (
        os.environ.get("XDG_RUNTIME_DIR", "").strip(),
        f"/run/user/{uid}",
        "/tmp",
    ):
        if candidate and os.path.isdir(candidate) and os.access(candidate, os.W_OK | os.X_OK):
            return Path(candidate) / f"phonebridge-{uid}.sock"
    return Path("/tmp") / f"phonebridge-{uid}.sock"


def run(cmd: list[str], *, timeout: float = 8.0) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "cmd": cmd,
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "cmd": cmd,
    }


def send_ipc(payload: bytes | str | dict, *, timeout: float = 1.0) -> None:
    if isinstance(payload, dict):
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = payload
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(str(socket_path()))
        conn.sendall(data)


def log_offset() -> int:
    if not LOG_PATH.exists():
        return 0
    return LOG_PATH.stat().st_size


def wait_for_log(pattern: str, *, timeout: float = 8.0, offset: int = 0) -> tuple[str | None, int]:
    regex = re.compile(pattern)
    deadline = time.monotonic() + timeout
    cursor = int(offset)
    while time.monotonic() < deadline:
        if LOG_PATH.exists():
            with LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(cursor)
                chunk = handle.read()
                cursor = handle.tell()
            for line in chunk.splitlines():
                if regex.search(line):
                    return line, cursor
        time.sleep(0.15)
    return None, cursor


def wait_until(predicate, *, timeout: float = 8.0, step: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def write_report(path: str | Path, report: dict) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return out
