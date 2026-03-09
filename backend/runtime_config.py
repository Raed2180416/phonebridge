"""Canonical runtime configuration contract for PhoneBridge public v1."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import backend.settings_store as settings


SUPPORTED_HOST = "NixOS"
SUPPORTED_COMPOSITOR = "Hyprland"
SUPPORTED_BRIDGE = "KDE Connect + ADB"

ENV_OVERRIDES = dict(settings.ENV_OVERRIDES)


def get(key: str, default: Any = None) -> Any:
    return settings.get(key, default)


def settings_path() -> Path:
    return Path(settings.SETTINGS_PATH).expanduser()


def documented_env_vars() -> tuple[str, ...]:
    return tuple(ENV_OVERRIDES.values())


def adb_target(default: str = "") -> str:
    return str(get("adb_target", default) or "").strip()


def device_id(default: str = "") -> str:
    return str(get("device_id", default) or "").strip()


def device_name(default: str = "Phone") -> str:
    return str(get("device_name", default) or default).strip() or default


def phone_tailscale_ip(default: str = "") -> str:
    return str(get("phone_tailscale_ip", default) or "").strip()


def host_tailscale_ip(default: str = "") -> str:
    return str(get("nixos_tailscale_ip", default) or "").strip()


def syncthing_url(default: str = "http://127.0.0.1:8384") -> str:
    value = str(get("syncthing_url", default) or "").strip()
    return (value or default).rstrip("/")


def syncthing_api_key(default: str = "") -> str:
    return str(get("syncthing_api_key", default) or "").strip()


def syncthing_config_path() -> Path:
    return Path.home() / ".config" / "syncthing" / "config.xml"


def shorten_home_path(value: str | os.PathLike[str]) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    home = str(Path.home())
    if not home:
        return raw
    if raw == home:
        return "~"
    prefix = home + os.sep
    if raw.startswith(prefix):
        return "~" + raw[len(home):]
    return raw


def phone_identity() -> dict[str, str]:
    return {
        "device_name": device_name(),
        "device_id": device_id(),
        "adb_target": adb_target(),
        "phone_tailscale_ip": phone_tailscale_ip(),
        "host_tailscale_ip": host_tailscale_ip(),
    }
