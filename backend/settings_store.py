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
    "adb_target":           "100.127.0.90:5555",
    "phone_tailscale_ip":   "100.127.0.90",
    "nixos_tailscale_ip":   "100.71.39.20",
    "device_name":          "Nothing Phone 3a Pro",
    "device_id":            "a9fe30c209da40d4bddce484a2c4112a",
    "clipboard_history":    [],
    "window_opacity":       94,
    "close_to_tray":        True,
    "auto_bt_connect":      True,
    "theme_variant":        "minimal_glass",
    "theme_name":           "slate",
    "surface_alpha_mode":   "auto_fallback",
    "motion_level":         "rich",
    "kde_integration_enabled": True,
}

_cache = None

def load():
    global _cache
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
            _cache = {**DEFAULTS, **data}
    except:
        _cache = dict(DEFAULTS)
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
