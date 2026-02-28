"""systemd user-service helpers for PhoneBridge autostart."""
from __future__ import annotations

from pathlib import Path
import subprocess

UNIT_NAME = "phonebridge.service"


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _run_systemctl(*args: str) -> tuple[bool, str]:
    cmd = ["systemctl", "--user", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False, "systemctl is not available in PATH"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    msg = err or out
    return proc.returncode == 0, msg


def write_unit_file(project_root: str) -> Path:
    root = Path(project_root).resolve()
    unit_file = _unit_path()
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[Unit]\n"
        "Description=PhoneBridge tray app\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={root}\n"
        f"ExecStart={root / 'run-venv-nix.sh'} --background\n"
        "Restart=on-failure\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    unit_file.write_text(content, encoding="utf-8")
    return unit_file


def is_enabled() -> bool:
    ok, _ = _run_systemctl("is-enabled", UNIT_NAME)
    return ok


def set_enabled(enabled: bool) -> tuple[bool, str]:
    if enabled:
        project_root = Path(__file__).resolve().parents[1]
        unit_path = write_unit_file(str(project_root))
        ok, msg = _run_systemctl("daemon-reload")
        if not ok:
            return False, msg or "systemctl daemon-reload failed"
        ok, msg = _run_systemctl("enable", "--now", UNIT_NAME)
        if not ok:
            return False, msg or "systemctl enable --now failed"
        return True, f"Start on Login enabled ({unit_path})"

    ok, msg = _run_systemctl("disable", "--now", UNIT_NAME)
    if not ok:
        # If the service is already disabled or missing, treat that as a disabled state.
        if not is_enabled():
            return True, "Start on Login already disabled"
        return False, msg or "systemctl disable --now failed"
    return True, "Start on Login disabled"
