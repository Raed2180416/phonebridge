"""Desktop/system integration helpers (icon, desktop entry, hotkey)."""
from __future__ import annotations

from pathlib import Path
import logging
import subprocess

import backend.autostart as autostart

log = logging.getLogger(__name__)

APP_ID = "phonebridge"
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4fd1c5"/>
      <stop offset="1" stop-color="#7c6cff"/>
    </linearGradient>
  </defs>
  <rect x="14" y="8" width="100" height="112" rx="22" fill="#111827"/>
  <rect x="20" y="14" width="88" height="100" rx="18" fill="url(#g)" opacity="0.22"/>
  <rect x="34" y="24" width="60" height="76" rx="10" fill="#0b1220" stroke="#cbd5e1" stroke-opacity="0.28"/>
  <circle cx="64" cy="92" r="4" fill="#e2e8f0" fill-opacity="0.8"/>
  <path d="M48 48h32M48 62h32M48 76h24" stroke="#b8c3d9" stroke-width="4" stroke-linecap="round"/>
</svg>
"""


def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        return False, str(exc)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return proc.returncode == 0, (err or out)


def _write_if_changed(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except Exception:
            existing = ""
    if existing == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def ensure_desktop_entry(project_root: Path) -> tuple[bool, str]:
    desktop_path = Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop"
    exec_path = project_root / "run-venv-nix.sh"
    icon_name = APP_ID
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=PhoneBridge\n"
        "Comment=Phone control center for desktop\n"
        f"Exec={exec_path}\n"
        f"TryExec={exec_path}\n"
        f"Icon={icon_name}\n"
        "Terminal=false\n"
        "Categories=Utility;Network;\n"
        "StartupNotify=true\n"
    )
    changed = _write_if_changed(desktop_path, content)
    if changed:
        desktop_path.chmod(0o755)
    _run(["update-desktop-database", str(desktop_path.parent)])
    return True, str(desktop_path)


def ensure_icon() -> tuple[bool, str]:
    icon_path = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    _write_if_changed(icon_path, ICON_SVG)
    _run(["gtk-update-icon-cache", "-f", "-t", str(icon_path.parents[2])])
    return True, str(icon_path)


def ensure_hyprland_toggle_binding(project_root: Path) -> tuple[bool, str]:
    config_dir = Path.home() / ".config" / "hypr"
    if not config_dir.exists():
        return False, "Hyprland config not found; skipped SUPER+P binding"
    bind_conf = config_dir / "phonebridge.conf"
    main_conf = config_dir / "hyprland.conf"
    toggle_cmd = f"{project_root / 'run-venv-nix.sh'} --toggle"
    browser_cmd = "zen"
    bind_lines = [
        f"bind = SUPER, P, exec, {toggle_cmd}\n",
        f"bind = SUPER, F, exec, {browser_cmd}\n",
    ]
    bind_content = (
        "# Managed by PhoneBridge\n"
        + "".join(bind_lines)
    )
    _write_if_changed(bind_conf, bind_content)

    if main_conf.exists():
        include_line = "source = ~/.config/hypr/phonebridge.conf\n"
        try:
            text = main_conf.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"Cannot read Hyprland config ({exc}); skipped SUPER+P binding"
        if include_line not in text:
            try:
                main_conf.write_text(text.rstrip() + "\n\n" + include_line, encoding="utf-8")
            except OSError as exc:
                return False, f"Cannot update Hyprland config ({exc}); skipped SUPER+P binding"
    _run(["hyprctl", "reload"])
    return True, str(bind_conf)


def ensure_system_integration(project_root: str) -> None:
    root = Path(project_root).resolve()
    try:
        ok, info = ensure_icon()
        if ok:
            log.info("Installed app icon: %s", info)
    except Exception:
        log.exception("Failed installing icon")

    try:
        ok, info = ensure_desktop_entry(root)
        if ok:
            log.info("Installed desktop entry: %s", info)
    except Exception:
        log.exception("Failed installing desktop entry")

    try:
        ok, info = ensure_hyprland_toggle_binding(root)
        if ok:
            log.info("Configured SUPER+P binding: %s", info)
        else:
            log.info(info)
    except Exception:
        log.exception("Failed configuring SUPER+P binding")

    try:
        if not autostart.is_enabled():
            ok, msg = autostart.set_enabled(True)
            if ok:
                log.info("Enabled startup service")
            else:
                log.warning("Could not enable startup service: %s", msg)
    except Exception:
        log.exception("Failed enabling startup service")
