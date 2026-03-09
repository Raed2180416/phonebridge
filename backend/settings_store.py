"""Persistent settings store"""
import json
import logging
import os
from pathlib import Path
import tempfile
import threading

SETTINGS_PATH = os.path.expanduser("~/.config/phonebridge/settings.json")
log = logging.getLogger(__name__)

DEFAULTS = {
    "suppress_calls":       False,
    "dnd_active":           False,
    "audio_redirect":       False,
    "clipboard_autoshare":  True,
    "phonesend_dir":        os.path.expanduser("~/PhoneSync/PhoneSend"),
    "sync_root":            os.path.expanduser("~/PhoneSync"),
    "adb_target":           "",
    "phone_tailscale_ip":   "",
    "nixos_tailscale_ip":   "",
    "device_name":          "Phone",
    "device_id":            "",
    "syncthing_url":        "http://127.0.0.1:8384",
    "syncthing_api_key":    "",
    "clipboard_history":    [],
    "window_opacity":       94,
    "close_to_tray":        True,
    "auto_bt_connect":      True,
    # Keep phone-call routing stable by dropping BT media profiles when idle.
    "bt_call_ready_mode":   True,
    "sync_on_mobile_data":  False,
    "missed_call_popups_enabled": True,
    "theme_name":           "slate",
    "motion_level":         "subtle",
    "kde_integration_enabled": True,
    "startup_check_on_login": False,
    "call_output_device":   "",
    "call_input_device":    "",
    "call_output_volume_pct": -1,
    "call_input_volume_pct": -1,
    "tailscale_force_off":  False,
    # Integration writes are opt-in by default for new installs.
    "integration_manage_icon": False,
    "integration_manage_desktop_entry": False,
    "integration_manage_hypr_bind": False,
    "integration_manage_autostart": False,
}

_cache = None
_lock = threading.RLock()

ENV_OVERRIDES = {
    "adb_target": "PHONEBRIDGE_ADB_TARGET",
    "phone_tailscale_ip": "PHONEBRIDGE_PHONE_TAILSCALE_IP",
    "nixos_tailscale_ip": "PHONEBRIDGE_HOST_TAILSCALE_IP",
    "device_name": "PHONEBRIDGE_DEVICE_NAME",
    "device_id": "PHONEBRIDGE_DEVICE_ID",
    "syncthing_url": "PHONEBRIDGE_SYNCTHING_URL",
    "syncthing_api_key": "PHONEBRIDGE_SYNCTHING_API_KEY",
}

CONSENT_MIGRATION_KEYS = (
    "integration_manage_icon",
    "integration_manage_desktop_entry",
    "integration_manage_hypr_bind",
    "integration_manage_autostart",
)

# Keys that were once in DEFAULTS but serve no purpose; purged on next load.
_DEAD_KEYS: frozenset[str] = frozenset({"theme_variant", "surface_alpha_mode"})


def _normalize_setting_value(key, value):
    if key == "theme_name":
        return "slate"
    return value


def _normalize_settings_map(values: dict):
    normalized = dict(values or {})
    if "theme_name" in normalized or "theme_name" in DEFAULTS:
        normalized["theme_name"] = "slate"
    return {key: _normalize_setting_value(key, value) for key, value in normalized.items()}


def _looks_like_phonebridge_desktop_entry(path):
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return False
    if "Name=PhoneBridge" not in text:
        return False
    return (
        "run-venv-runtime.sh" in text
        or "runtime/current" in text
        or "run-venv-nix.sh" in text
    )


def _looks_like_phonebridge_hypr_bind(path):
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return False
    return (
        "# Managed by PhoneBridge" in text
        and "--toggle" in text
        and "bind = SUPER, P, exec," in text
    )


def _infer_legacy_integration_consent():
    icon_path = os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/phonebridge.svg")
    desktop_path = os.path.expanduser("~/.local/share/applications/phonebridge.desktop")
    hypr_bind_path = os.path.expanduser("~/.config/hypr/phonebridge.conf")
    autostart_unit = os.path.expanduser("~/.config/systemd/user/phonebridge.service")
    autostart_wants = os.path.expanduser("~/.config/systemd/user/default.target.wants/phonebridge.service")

    return {
        "integration_manage_icon": os.path.exists(icon_path),
        "integration_manage_desktop_entry": (
            os.path.exists(desktop_path) and _looks_like_phonebridge_desktop_entry(desktop_path)
        ),
        "integration_manage_hypr_bind": (
            os.path.exists(hypr_bind_path) and _looks_like_phonebridge_hypr_bind(hypr_bind_path)
        ),
        "integration_manage_autostart": (
            os.path.exists(autostart_unit) or os.path.exists(autostart_wants)
        ),
    }


def _apply_consent_migration(merged, loaded_data, had_settings_file):
    # Fresh installs keep strict opt-in defaults.
    if not had_settings_file:
        return merged

    loaded_data = loaded_data or {}
    missing = [k for k in CONSENT_MIGRATION_KEYS if k not in loaded_data]
    if not missing:
        return merged

    inferred = _infer_legacy_integration_consent()
    for key in missing:
        merged[key] = bool(inferred.get(key, False))
    return merged


def _apply_env_overrides(data):
    for key, env_name in ENV_OVERRIDES.items():
        val = os.environ.get(env_name)
        if val is None:
            continue
        value = str(val).strip()
        if value:
            data[key] = value
    return data

def load():
    global _cache
    with _lock:
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                merged = {**DEFAULTS, **data}
                merged = _apply_consent_migration(merged, data, had_settings_file=True)
                for k in _DEAD_KEYS:
                    merged.pop(k, None)
                _cache = _normalize_settings_map(_apply_env_overrides(merged))
        except Exception:
            log.debug("Settings load fell back to defaults", exc_info=True)
            _cache = _normalize_settings_map(_apply_env_overrides(dict(DEFAULTS)))
        return dict(_cache)

def get(key, default=None):
    with _lock:
        if _cache is None:
            load()
        return _cache.get(key, default if default is not None else DEFAULTS.get(key))

def set(key, value):
    set_many({key: value})

def set_many(values: dict):
    if not values:
        return
    with _lock:
        if _cache is None:
            load()
        _cache.update(_normalize_settings_map(values))
        save_locked()

def save():
    with _lock:
        if _cache is None:
            load()
        save_locked()


def save_locked():
    data = _normalize_settings_map(_cache or DEFAULTS)
    path = Path(SETTINGS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        log.exception("Settings save failed path=%s", SETTINGS_PATH)
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass
