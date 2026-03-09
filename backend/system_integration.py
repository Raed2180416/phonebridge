"""Desktop/system integration helpers (icon, desktop entry, hotkey)."""
from __future__ import annotations

from pathlib import Path
import logging
import subprocess

import backend.autostart as autostart
from backend import hyprland
import backend.settings_store as settings

log = logging.getLogger(__name__)

APP_ID = "phonebridge"
HYPR_INCLUDE_LINE = "source = ~/.config/hypr/phonebridge.conf\n"
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


def desktop_entry_path() -> Path:
    return Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop"


def desktop_entry_contents(project_root: Path) -> str:
    exec_path = autostart.preferred_launcher(project_root)
    icon_name = APP_ID
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=PhoneBridge\n"
        "Comment=Phone control center for desktop\n"
        f"Exec={exec_path}\n"
        f"TryExec={exec_path}\n"
        f"Icon={icon_name}\n"
        "StartupWMClass=phonebridge\n"
        "X-GNOME-WMClass=phonebridge\n"
        "Terminal=false\n"
        "Categories=Utility;Network;\n"
        "StartupNotify=true\n"
    )


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
    desktop_path = desktop_entry_path()
    content = desktop_entry_contents(project_root)
    changed = _write_if_changed(desktop_path, content)
    if changed:
        desktop_path.chmod(0o755)
    _run(["update-desktop-database", str(desktop_path.parent)])
    return True, str(desktop_path)


def refresh_desktop_entry_if_present(project_root: Path) -> tuple[bool, str]:
    desktop_path = desktop_entry_path()
    should_refresh = desktop_path.exists() or bool(settings.get("integration_manage_desktop_entry", False))
    if not should_refresh:
        return False, "Desktop entry absent and auto-management disabled"
    return ensure_desktop_entry(project_root)


def ensure_icon() -> tuple[bool, str]:
    icon_path = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    _write_if_changed(icon_path, ICON_SVG)
    _run(["gtk-update-icon-cache", "-f", "-t", str(icon_path.parents[2])])
    return True, str(icon_path)


def ensure_hyprland_call_popup_rules() -> tuple[bool, str]:
    return hyprland.ensure_call_popup_rules()


def ensure_hyprland_toggle_binding(project_root: Path) -> tuple[bool, str]:
    config_dir = Path.home() / ".config" / "hypr"
    if not config_dir.exists():
        return False, "Hyprland config not found; skipped SUPER+P binding"
    bind_conf = config_dir / "phonebridge.conf"
    main_conf = config_dir / "hyprland.conf"
    toggle_script = project_root / "scripts" / "phonebridge-toggle.sh"
    if toggle_script.exists():
        toggle_cmd = f"{toggle_script}"
    else:
        toggle_cmd = f"{autostart.preferred_launcher(project_root)} --toggle"

    # Check if Home Manager / NixOS already declares a SUPER+P phonebridge
    # keybind in hyprland.conf.
    main_already_has_bind = False
    main_text = ""
    if main_conf.exists():
        try:
            main_text = main_conf.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"Cannot read Hyprland config ({exc}); skipped SUPER+P binding"
        for line in main_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "SUPER" in stripped and ", P," in stripped and "phonebridge" in stripped:
                main_already_has_bind = True
                break

    if main_already_has_bind:
        # External config manages keybind. Clean up our managed file if present.
        if bind_conf.exists():
            try:
                bind_conf.unlink()
            except OSError:
                pass
        # Clean up source line if present.
        if HYPR_INCLUDE_LINE in main_text:
            try:
                main_conf.write_text(
                    main_text.replace(HYPR_INCLUDE_LINE, ""),
                    encoding="utf-8",
                )
            except OSError:
                pass  # Read-only (Nix store) is fine
        # Always inject call-popup windowrules via IPC.
        ensure_hyprland_call_popup_rules()
        return True, "SUPER+P keybind already managed by system config; skipped"

    # Write our own keybind config file.
    bind_content = (
        "# Managed by PhoneBridge\n"
        f"bind = SUPER, P, exec, {toggle_cmd}\n"
    )
    _write_if_changed(bind_conf, bind_content)

    if main_conf.exists() and HYPR_INCLUDE_LINE not in main_text:
        try:
            main_conf.write_text(
                main_text.rstrip() + "\n\n" + HYPR_INCLUDE_LINE,
                encoding="utf-8",
            )
        except OSError as exc:
            log.debug("Cannot update Hyprland config: %s", exc)

    _hyprctl_reload()
    # Always inject call-popup windowrules via IPC.
    ensure_hyprland_call_popup_rules()
    return True, str(bind_conf)


def _hyprctl_reload():
    """Reload Hyprland config via IPC socket (works inside bwrap)."""
    hyprland.reload_config()


def _hyprland_socket_path() -> str | None:
    """Backward-compatible socket-path wrapper."""
    return hyprland.socket_path()


def _hyprland_ipc(sock_path: str, command: bytes) -> str:
    """Backward-compatible IPC wrapper."""
    return hyprland.ipc(command, sock_path=sock_path)


def disable_desktop_entry() -> tuple[bool, str]:
    desktop_path = desktop_entry_path()
    try:
        if desktop_path.exists():
            desktop_path.unlink()
        _run(["update-desktop-database", str(desktop_path.parent)])
        return True, "Desktop entry management disabled"
    except OSError as exc:
        return False, f"Could not disable desktop entry management ({exc})"


def disable_icon() -> tuple[bool, str]:
    icon_path = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    try:
        if icon_path.exists():
            icon_path.unlink()
        _run(["gtk-update-icon-cache", "-f", "-t", str(icon_path.parents[2])])
        return True, "Icon management disabled"
    except OSError as exc:
        return False, f"Could not disable icon management ({exc})"


def disable_hyprland_toggle_binding() -> tuple[bool, str]:
    config_dir = Path.home() / ".config" / "hypr"
    bind_conf = config_dir / "phonebridge.conf"
    main_conf = config_dir / "hyprland.conf"
    try:
        if bind_conf.exists():
            bind_conf.unlink()
        if main_conf.exists():
            text = main_conf.read_text(encoding="utf-8")
            if HYPR_INCLUDE_LINE in text:
                main_conf.write_text(text.replace(HYPR_INCLUDE_LINE, ""), encoding="utf-8")
        _hyprctl_reload()
        return True, "Hyprland PhoneBridge bind management disabled"
    except OSError as exc:
        return False, f"Could not disable Hyprland bind management ({exc})"


def set_desktop_entry_management(project_root: str, enabled: bool) -> tuple[bool, str]:
    settings.set("integration_manage_desktop_entry", bool(enabled))
    if enabled:
        return ensure_desktop_entry(Path(project_root).resolve())
    return disable_desktop_entry()


def set_icon_management(enabled: bool) -> tuple[bool, str]:
    settings.set("integration_manage_icon", bool(enabled))
    if enabled:
        return ensure_icon()
    return disable_icon()


def set_hyprland_binding_management(project_root: str, enabled: bool) -> tuple[bool, str]:
    settings.set("integration_manage_hypr_bind", bool(enabled))
    if enabled:
        return ensure_hyprland_toggle_binding(Path(project_root).resolve())
    return disable_hyprland_toggle_binding()


def set_autostart_management(enabled: bool) -> tuple[bool, str]:
    settings.set("integration_manage_autostart", bool(enabled))
    if not enabled:
        return True, "Autostart auto-management disabled"
    if autostart.is_enabled():
        return True, "Autostart already enabled"
    return autostart.set_enabled(True)


def ensure_system_integration(project_root: str) -> None:
    root = Path(project_root).resolve()
    if settings.get("integration_manage_icon", False):
        try:
            ok, info = ensure_icon()
            if ok:
                log.info("Installed app icon: %s", info)
        except Exception:
            log.exception("Failed installing icon")
    else:
        log.info("Skipped icon install (opt-in disabled)")

    if settings.get("integration_manage_desktop_entry", False):
        try:
            ok, info = ensure_desktop_entry(root)
            if ok:
                log.info("Installed desktop entry: %s", info)
        except Exception:
            log.exception("Failed installing desktop entry")
    else:
        log.info("Skipped desktop entry install (opt-in disabled)")

    if settings.get("integration_manage_hypr_bind", False):
        try:
            ok, info = ensure_hyprland_toggle_binding(root)
            if ok:
                log.info("Configured SUPER+P binding: %s", info)
            else:
                log.info(info)
        except Exception:
            log.exception("Failed configuring SUPER+P binding")
    else:
        log.info("Skipped Hyprland binding (opt-in disabled)")

    if settings.get("integration_manage_autostart", False):
        try:
            if not autostart.is_enabled():
                ok, msg = autostart.set_enabled(True)
                if ok:
                    log.info("Enabled startup service")
                else:
                    log.warning("Could not enable startup service: %s", msg)
        except Exception:
            log.exception("Failed enabling startup service")
    else:
        log.info("Skipped autostart enable (opt-in disabled)")
