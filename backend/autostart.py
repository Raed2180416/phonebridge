"""systemd user-service helpers for PhoneBridge autostart."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import logging

UNIT_NAME = "phonebridge.service"
log = logging.getLogger(__name__)


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _runtime_base_path() -> Path:
    return Path.home() / ".local" / "share" / "phonebridge" / "runtime"


def _runtime_current_path() -> Path:
    return _runtime_base_path() / "current"


def _write_text_atomic(path: Path, content: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.chmod(mode)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


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


def _runtime_launcher_contents(project_root: Path) -> str:
    venv_python = project_root / ".venv" / "bin" / "python"
    return (
        "#!/bin/sh\n"
        "if [ -z \"${BASH_VERSION:-}\" ]; then\n"
        "  if [ -x /run/current-system/sw/bin/bash ]; then\n"
        "    exec /run/current-system/sw/bin/bash \"$0\" \"$@\"\n"
        "  fi\n"
        "  if command -v bash >/dev/null 2>&1; then\n"
        "    exec \"$(command -v bash)\" \"$0\" \"$@\"\n"
        "  fi\n"
        "  echo \"bash is required but was not found.\" >&2\n"
        "  exit 127\n"
        "fi\n"
        "set -euo pipefail\n"
        'RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'VENV_PY="{venv_python}"\n'
        'if [[ ! -x "$VENV_PY" ]]; then\n'
        '  echo "Missing venv python: $VENV_PY" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if ! command -v steam-run >/dev/null 2>&1; then\n'
        '  echo "steam-run is required but not found in PATH." >&2\n'
        "  exit 1\n"
        "fi\n"
        'declare -a PY_CANDIDATES=(\n'
        '  "/etc/profiles/per-user/${USER}/bin/python"\n'
        '  "/etc/profiles/per-user/${USER}/bin/python3"\n'
        '  "/run/current-system/sw/bin/python3"\n'
        '  "/nix/var/nix/profiles/default/bin/python3"\n'
        ")\n"
        'if command -v python3 >/dev/null 2>&1; then\n'
        '  PY_CANDIDATES+=("$(command -v python3)")\n'
        "fi\n"
        'if command -v python >/dev/null 2>&1; then\n'
        '  PY_CANDIDATES+=("$(command -v python)")\n'
        "fi\n"
        'SYSTEM_PY=""\n'
        'for cand in "${PY_CANDIDATES[@]}"; do\n'
        '  if [[ -x "$cand" ]]; then\n'
        '    SYSTEM_PY="$cand"\n'
        "    break\n"
        "  fi\n"
        "done\n"
        'if [[ -z "$SYSTEM_PY" ]]; then\n'
        '  echo "Could not find a system python interpreter for dbus site-packages lookup." >&2\n'
        "  exit 1\n"
        "fi\n"
        'SYS_SITE="$("$SYSTEM_PY" - <<\'PY\'\n'
        "import site\n"
        "print(site.getsitepackages()[0])\n"
        "PY\n"
        ')"\n'
        'export PYTHONPATH="$SYS_SITE${PYTHONPATH:+:$PYTHONPATH}"\n'
        'exec steam-run "$VENV_PY" "$RUNTIME_DIR/main.py" "$@"\n'
    )


def _ipc_base_dir() -> Path:
    uid = os.getuid()
    candidates = [
        os.environ.get("XDG_RUNTIME_DIR", "").strip(),
        f"/run/user/{uid}",
        "/tmp",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
            return path
    return Path("/tmp")


def _candidate_socket_paths() -> list[Path]:
    uid = os.getuid()
    primary = _ipc_base_dir() / f"phonebridge-{uid}.sock"
    legacy = Path("/tmp") / f"phonebridge-{uid}.sock"
    return [primary] if primary == legacy else [primary, legacy]


def _send_ipc(command: bytes, *, timeout: float = 0.5) -> bool:
    for path in _candidate_socket_paths():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(timeout)
                conn.connect(str(path))
                conn.sendall(command)
            return True
        except OSError:
            continue
    return False


def _service_is_active() -> bool:
    ok, _ = _run_systemctl("is-active", UNIT_NAME)
    return ok


def preferred_launcher(project_root: str | Path) -> Path:
    root = Path(project_root).resolve()
    current_launcher = _runtime_current_path() / "run-venv-runtime.sh"
    if current_launcher.exists():
        return current_launcher
    runtime_launcher = root / "run-venv-runtime.sh"
    if runtime_launcher.exists():
        return runtime_launcher
    return root / "run-venv-nix.sh"


def _prune_old_releases(runtime_base: Path, *, keep: int = 4) -> None:
    current = _runtime_current_path()
    try:
        current_resolved = current.resolve(strict=False)
    except Exception:
        current_resolved = None
    releases = []
    for child in runtime_base.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name == "current":
            continue
        releases.append(child)
    releases.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    kept = 0
    for release in releases:
        try:
            resolved = release.resolve(strict=False)
        except Exception:
            resolved = release
        if current_resolved is not None and resolved == current_resolved:
            kept += 1
            continue
        if kept < int(keep):
            kept += 1
            continue
        shutil.rmtree(release, ignore_errors=True)


def publish_runtime(project_root: str) -> tuple[Path, Path]:
    root = Path(project_root).resolve()
    runtime_base = _runtime_base_path()
    runtime_base.mkdir(parents=True, exist_ok=True)
    version = time.strftime("release-%Y%m%d-%H%M%S")
    release_dir = runtime_base / version
    if release_dir.exists():
        suffix = tempfile.mkstemp(prefix="dup-", dir=str(runtime_base))[1]
        Path(suffix).unlink(missing_ok=True)
        release_dir = runtime_base / f"{version}-{Path(suffix).name}"
    stage_dir = runtime_base / f".staging-{release_dir.name}-{os.getpid()}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    ignore = shutil.ignore_patterns(
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "*.pyc",
        "*.pyo",
    )
    shutil.copytree(root, stage_dir, symlinks=True, ignore=ignore)
    launcher = stage_dir / "run-venv-runtime.sh"
    _write_text_atomic(launcher, _runtime_launcher_contents(root), mode=0o755)
    launcher.chmod(0o755)
    stage_dir.replace(release_dir)

    current = _runtime_current_path()
    tmp_link = runtime_base / ".current.tmp"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    tmp_link.symlink_to(release_dir, target_is_directory=True)
    tmp_link.replace(current)
    _prune_old_releases(runtime_base)
    try:
        from backend import system_integration

        ok, info = system_integration.refresh_desktop_entry_if_present(root)
        if ok:
            log.info("Refreshed installed desktop entry after publish: %s", info)
        else:
            log.info("Skipped desktop-entry refresh after publish: %s", info)
    except Exception:
        log.exception("Failed refreshing installed desktop entry after publish")
    return current, current / "run-venv-runtime.sh"


def restart_running_app(launcher: str | Path | None = None) -> tuple[bool, str]:
    if _service_is_active():
        ok, msg = _run_systemctl("restart", UNIT_NAME)
        if ok:
            return True, "restarted systemd user service"
        return False, msg or "systemctl restart failed"

    if _send_ipc(b"quit", timeout=0.35):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not any(path.exists() for path in _candidate_socket_paths()):
                break
            time.sleep(0.1)
    if launcher is None:
        return True, "no running service detected; publish only"
    launch_path = Path(launcher)
    try:
        subprocess.Popen(
            [str(launch_path), "--background"],
            cwd=str(launch_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, f"launched {launch_path}"
    except Exception as exc:
        return False, f"failed launching {launch_path}: {exc}"


def write_unit_file(project_root: str) -> Path:
    runtime_root, launcher = publish_runtime(project_root)
    unit_file = _unit_path()
    unit_file.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[Unit]\n"
        "Description=PhoneBridge tray app\n"
        "After=default.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={runtime_root}\n"
        f"ExecStart={launcher} --background\n"
        "Restart=on-failure\n"
        "RestartSec=2\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    _write_text_atomic(unit_file, content)
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
