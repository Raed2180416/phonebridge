"""Hyprland IPC adapter for PhoneBridge."""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess

log = logging.getLogger(__name__)

CALL_POPUP_SELECTOR = "title:^(PhoneBridge Call)$"
CALL_POPUP_RULES = [
    "float on, match:title ^(PhoneBridge Call)$",
    "pin on, match:title ^(PhoneBridge Call)$",
    "no_shadow on, match:title ^(PhoneBridge Call)$",
    "no_anim on, match:title ^(PhoneBridge Call)$",
    "move 100%-320 54, match:title ^(PhoneBridge Call)$",
]


def socket_path() -> str | None:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature:
        return None
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    for candidate in (
        os.path.join(xdg_runtime, "hypr", signature, ".socket.sock"),
        f"/tmp/hypr/{signature}/.socket.sock",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def ipc(command: bytes, *, sock_path: str | None = None) -> str:
    path = sock_path or socket_path()
    if not path:
        return ""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(0.08)
            conn.connect(path)
            conn.sendall(command)
            chunks = []
            while True:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                except socket.timeout:
                    break
        return b"".join(chunks).decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("Hyprland IPC failed: %s", exc)
        return ""


def reload_config() -> None:
    path = socket_path()
    if path:
        ipc(b"reload", sock_path=path)
        return
    try:
        subprocess.run(["hyprctl", "reload"], capture_output=True, text=True, timeout=1.5, check=False)
    except Exception:
        log.debug("Hyprland reload fallback failed", exc_info=True)


def ensure_call_popup_rules() -> tuple[bool, str]:
    path = socket_path()
    if not path:
        return False, "Hyprland socket not found; skipped call popup rules"
    errors = []
    for rule in CALL_POPUP_RULES:
        response = ipc(f"/keyword windowrule {rule}".encode(), sock_path=path)
        if response and "ok" not in response.lower() and "err" in response.lower():
            errors.append(f"{rule}: {response}")
    if errors:
        return False, f"Some rules failed: {errors}"
    log.info("Injected %d Hyprland call-popup windowrules via IPC", len(CALL_POPUP_RULES))
    return True, f"Injected {len(CALL_POPUP_RULES)} call-popup windowrules"


def move_window_exact(selector: str, x: int, y: int) -> bool:
    path = socket_path()
    if not path:
        return False
    response = ipc(f"dispatch movewindowpixel exact {int(x)} {int(y)},{selector}".encode(), sock_path=path)
    return (not response) or ("err" not in response.lower())


def alterzorder_top(selector: str) -> bool:
    path = socket_path()
    if not path:
        return False
    response = ipc(f"dispatch alterzorder top,{selector}".encode(), sock_path=path)
    return (not response) or ("err" not in response.lower())


def set_floating_pinned_top(selector: str) -> bool:
    path = socket_path()
    if not path:
        return False
    ipc(f"dispatch setfloating {selector}".encode(), sock_path=path)
    ipc(f"dispatch pin {selector}".encode(), sock_path=path)
    response = ipc(f"dispatch alterzorder top,{selector}".encode(), sock_path=path)
    return (not response) or ("err" not in response.lower())


def focus_window(selector: str) -> bool:
    path = socket_path()
    if not path or not selector:
        return False
    response = ipc(f"dispatch focuswindow {selector}".encode(), sock_path=path)
    return (not response) or ("err" not in response.lower())


def capture_active_window_selector(
    *,
    exclude_titles: set[str] | None = None,
    exclude_classes: set[str] | None = None,
) -> str | None:
    try:
        proc = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        row = json.loads(proc.stdout)
    except Exception:
        return None
    title = str(row.get("title") or "")
    window_class = str(row.get("class") or "").lower()
    if exclude_titles and title in exclude_titles:
        return None
    if exclude_classes and window_class in {item.lower() for item in exclude_classes}:
        return None
    address = str(row.get("address") or "").strip()
    if not address:
        return None
    return f"address:{address}"


def move_pid_to_active_workspace(pid: int) -> bool:
    try:
        proc = subprocess.run(
            ["hyprctl", "dispatch", "movetoworkspacesilent", f"+0,pid:{int(pid)}"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0
