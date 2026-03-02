"""Dependency preflight checks for optional external binaries.

Call `get()` once at startup (cached thereafter). Each feature entry
maps to a structured result describing availability and fallback behavior.
Missing dependencies emit a single log warning and are never silently ignored.
"""

from __future__ import annotations

import logging
import shutil

log = logging.getLogger(__name__)

# (feature_id, ordered_candidates, human description, fallback note)
_OPTIONAL_DEPS: list[tuple[str, list[str], str, str]] = [
    (
        "audio_ctl",
        ["pactl", "wpctl"],
        "Audio device control CLI (call audio routing)",
        "Call audio routing unavailable without pactl or wpctl",
    ),
    (
        "thumbnails",
        ["ffmpegthumbnailer", "ffmpeg"],
        "Video/image thumbnail generator for file browser",
        "File browser uses icon fallback without ffmpegthumbnailer/ffmpeg",
    ),
    (
        "clipboard_wl",
        ["wl-copy"],
        "Wayland clipboard write tool",
        "Notification copy-to-clipboard falls back to xclip on X11",
    ),
    (
        "clipboard_x11",
        ["xclip"],
        "X11 clipboard write tool",
        "Notification copy-to-clipboard falls back to wl-copy on Wayland",
    ),
    (
        "mirror",
        ["scrcpy"],
        "Phone screen mirror/webcam (scrcpy)",
        "Screen Mirror and Webcam modes disabled without scrcpy",
    ),
    (
        "adb",
        ["adb"],
        "Android Debug Bridge",
        "ADB-dependent features (battery, screenshot, toggle controls) unavailable",
    ),
    (
        "bluetooth_ctl",
        ["bluetoothctl"],
        "Bluetooth control CLI",
        "Bluetooth toggle/pairing features unavailable without bluetoothctl",
    ),
    (
        "syncthing",
        ["syncthing"],
        "Syncthing folder sync daemon",
        "Sync page features unavailable without syncthing",
    ),
    (
        "tailscale",
        ["tailscale"],
        "Tailscale mesh VPN CLI",
        "Tailscale features unavailable without tailscale",
    ),
    (
        "notify_send",
        ["notify-send"],
        "Desktop notification sender",
        "Notification mirror falls back to D-Bus direct",
    ),
]

_cache: dict[str, dict] | None = None
_warned: set[str] = set()


def check_all() -> dict[str, dict]:
    """Run all preflight checks and return a feature → status dict."""
    results: dict[str, dict] = {}
    for feature, candidates, desc, fallback in _OPTIONAL_DEPS:
        found: str | None = None
        for cmd in candidates:
            if shutil.which(cmd):
                found = cmd
                break
        ok = found is not None
        if not ok and feature not in _warned:
            log.warning(
                "Optional dependency missing: feature=%s candidates=%s — %s",
                feature,
                ",".join(candidates),
                fallback,
            )
            _warned.add(feature)
        results[feature] = {
            "ok": ok,
            "found": found,
            "candidates": candidates,
            "description": desc,
            "fallback": fallback,
        }
    return results


def get() -> dict[str, dict]:
    """Return cached preflight results (computed once per process)."""
    global _cache
    if _cache is None:
        _cache = check_all()
    return _cache


def has(feature: str) -> bool:
    """Return True iff at least one candidate binary is present for `feature`."""
    return bool(get().get(feature, {}).get("ok", False))


def missing_text(feature: str) -> str:
    """Human-readable explanation when a feature dependency is missing.

    Returns empty string when the feature is available.
    """
    info = get().get(feature)
    if not info:
        return f"Feature '{feature}' not registered in preflight"
    if info["ok"]:
        return ""
    cands = " / ".join(info["candidates"])
    return f"Missing: {cands} — {info['fallback']}"


def summary_lines() -> list[str]:
    """Return a list of human-readable warning lines for all missing deps."""
    out: list[str] = []
    for feature, info in get().items():
        if not info["ok"]:
            out.append(
                f"⚠ {info['description']}: requires {' / '.join(info['candidates'])} — {info['fallback']}"
            )
    return out
