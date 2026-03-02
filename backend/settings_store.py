"""Persistent settings store"""
import json, os

SETTINGS_PATH = os.path.expanduser("~/.config/phonebridge/settings.json")

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
    "theme_name":           "slate",
    "motion_level":         "subtle",
    "kde_integration_enabled": True,
    "startup_check_on_login": True,
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


def _looks_like_phonebridge_desktop_entry(path):
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return False
    return "Name=PhoneBridge" in text and "run-venv-nix.sh" in text


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
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
            merged = {**DEFAULTS, **data}
            merged = _apply_consent_migration(merged, data, had_settings_file=True)
            for k in _DEAD_KEYS:
                merged.pop(k, None)
            _cache = _apply_env_overrides(merged)
    except:
        _cache = _apply_env_overrides(dict(DEFAULTS))
    return _cache

def get(key, default=None):
    if _cache is None:
        load()
    return _cache.get(key, default if default is not None else DEFAULTS.get(key))

def set(key, value):
    if _cache is None:
        load()
    _cache[key] = value
    save()

def save():
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(_cache or DEFAULTS, f, indent=2)
