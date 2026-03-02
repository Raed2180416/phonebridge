#!/usr/bin/env python3
"""Phone-triggered KDE Connect host actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import importlib
import os
import subprocess
import sys


LOG_PREFIX = "[kde-remote-actions]"


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repo_import_path() -> None:
    root = str(_project_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _session_id() -> str:
    return str(os.environ.get("XDG_SESSION_ID", "") or "").strip()


def _run_until_success(candidates: list[list[str]], timeout: float = 8.0) -> bool:
    for cmd in candidates:
        res = run_cmd(cmd, timeout=timeout)
        if res.ok:
            log(f"command ok: {' '.join(cmd)}")
            return True
        err = (res.stderr or res.stdout).strip()
        log(f"command failed ({res.returncode}): {' '.join(cmd)} :: {err}")
    return False


def action_lock_laptop() -> int:
    session_id = _session_id()
    cmds: list[list[str]] = []
    if session_id:
        cmds.append(["loginctl", "lock-session", session_id])
    cmds.extend(
        [
            ["loginctl", "lock-session", "self"],
            ["hyprlock"],
        ]
    )
    return 0 if _run_until_success(cmds, timeout=8.0) else 1


def action_shutdown_laptop() -> int:
    res = run_cmd(["systemctl", "poweroff", "-i"], timeout=8.0)
    if res.ok:
        log("shutdown command issued")
        return 0
    err = (res.stderr or res.stdout).strip()
    log(f"shutdown failed ({res.returncode}): {err}")
    return 1


def action_logout_laptop() -> int:
    session_id = _session_id()
    cmds: list[list[str]] = [["hyprctl", "dispatch", "exit"]]
    if session_id:
        cmds.append(["loginctl", "terminate-session", session_id])
    return 0 if _run_until_success(cmds, timeout=8.0) else 1


def ensure_phonebridge_background() -> bool:
    cmd = [str(_project_root() / "run-venv-nix.sh"), "--background"]
    res = run_cmd(cmd, timeout=20.0)
    if not res.ok:
        err = (res.stderr or res.stdout).strip()
        log(f"background ensure failed ({res.returncode}): {err}")
        return False
    return True


def _toggle_audio(enabled: bool) -> int:
    # Best-effort start. We still proceed with backend toggles even if startup command fails.
    ensure_phonebridge_background()
    _ensure_repo_import_path()
    try:
        audio_route = importlib.import_module("backend.audio_route")
    except Exception as exc:
        log(f"import backend.audio_route failed: {exc}")
        return 2
    try:
        audio_route.set_enabled(bool(enabled))
        ok = bool(audio_route.sync())
    except Exception as exc:
        log(f"audio route apply failed: {exc}")
        return 2
    if ok:
        log(f"audio route set to {'pc' if enabled else 'phone'}")
        return 0
    log("audio route sync reported failure")
    return 1


def action_audio_to_phone() -> int:
    return _toggle_audio(False)


def action_audio_to_pc() -> int:
    return _toggle_audio(True)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        log("usage: kde_remote_actions.py <lock-laptop|shutdown-laptop|logout-laptop|audio-to-phone|audio-to-pc>")
        return 2

    action = str(args[0] or "").strip().lower()
    if action == "lock-laptop":
        return action_lock_laptop()
    if action == "shutdown-laptop":
        return action_shutdown_laptop()
    if action == "logout-laptop":
        return action_logout_laptop()
    if action == "audio-to-phone":
        return action_audio_to_phone()
    if action == "audio-to-pc":
        return action_audio_to_pc()

    log(f"unknown action: {action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
