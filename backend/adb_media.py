"""Media, scrcpy, and recording helpers for the ADB bridge facade."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time


def launch_scrcpy(bridge, mode="mirror", extra_args=None, env_overrides=None):
    """Launch scrcpy with the known-good flags for this system."""
    env = os.environ.copy()
    env["WAYLAND_DISPLAY"] = "wayland-1"
    if env_overrides:
        env.update(env_overrides)

    audio_quality_flags = [
        "--audio-codec=aac",
        "--audio-bit-rate=256K",
        "--audio-buffer=120",
        "--audio-output-buffer=10",
    ]

    serial = bridge._resolve_target(allow_connect=True)
    if not serial:
        bridge.log.warning("Failed to launch scrcpy mode=%s: no adb target connected", mode)
        return None

    cmds = {
        "mirror": [
            "scrcpy", "--serial", serial,
            "--video-bit-rate", "8M",
            "--audio-source", "output",
            "--max-size", "1920",
            "--render-driver", "opengl",
            *audio_quality_flags,
        ],
        "webcam": [
            "scrcpy", "--serial", serial,
            "--video-source=camera",
            "--camera-facing=front",
            "--camera-size=1280x720",
            "--window-title", "PhoneBridge Webcam",
            "--render-driver", "opengl",
        ],
        "audio": [
            "scrcpy", "--serial", serial,
            "--audio-source=voice-call",
            "--no-video",
            "--no-window",
        ],
        "audio_output": [
            "scrcpy", "--serial", serial,
            "--audio-source=output",
            "--no-video",
            "--no-window",
            *audio_quality_flags,
        ],
    }
    cmd = list(cmds.get(mode, cmds["mirror"]))
    if extra_args:
        cmd.extend(list(extra_args))
    bridge.log.info("Launching scrcpy mode=%s target=%s", mode, serial)
    try:
        return subprocess.Popen(cmd, env=env)
    except Exception:
        bridge.log.exception("Failed to launch scrcpy mode=%s", mode)
        return None


def screenshot(bridge):
    path = os.path.expanduser(f"~/Pictures/phone_{int(time.time())}.png")
    bridge._run("exec-out", "screencap", "-p")
    bridge._run("shell", "screencap", "-p", "/sdcard/tmp_screenshot.png")
    ok, _ = bridge._run("pull", "/sdcard/tmp_screenshot.png", path)
    if ok:
        bridge._run("shell", "rm", "/sdcard/tmp_screenshot.png")
    return path if ok else None


def get_now_playing(bridge, preferred_package: str = ""):
    ok, out = bridge._run("shell", "dumpsys", "media_session", timeout=4)
    if not ok:
        return None
    sessions = []
    current = None
    in_metadata = False
    for raw in out.splitlines():
        line = raw.rstrip()
        header = re.match(r"^\s{4}(.+?) ([a-zA-Z0-9\._]+)/[^/]+/\d+ \(userId=\d+\)$", line)
        if header:
            if current:
                sessions.append(current)
            current = {
                "session_name": header.group(1).strip(),
                "package": header.group(2).strip(),
                "active": False,
                "state": "",
                "title": "",
                "artist": "",
                "album": "",
                "artwork": "",
                "media_uri": "",
            }
            in_metadata = False
            continue
        if not current:
            continue
        if "active=true" in line:
            current["active"] = True
        if "state=PlaybackState" in line:
            match = re.search(r"state=([A-Z_]+)\(", line)
            if match:
                current["state"] = match.group(1).lower()
        if "metadata:" in line and "description=" in line:
            desc = line.split("description=", 1)[1].strip()
            parts = [part.strip() for part in desc.split(",")]
            if parts:
                current["title"] = parts[0]
            if len(parts) > 1:
                current["artist"] = parts[1]
            if len(parts) > 2:
                current["album"] = parts[2]
        if re.match(r"^\s*metadata:", line):
            in_metadata = True
        elif in_metadata and re.match(r"^\s{4}\S", line):
            in_metadata = False
        if in_metadata:
            kv = line.strip()
            if "ALBUM_ART_URI=" in kv:
                current["artwork"] = kv.split("ALBUM_ART_URI=", 1)[1].strip()
            elif "ART_URI=" in kv and "ALBUM_ART_URI=" not in kv:
                current["artwork"] = kv.split("ART_URI=", 1)[1].strip()
            elif "DISPLAY_ICON_URI=" in kv and not current.get("artwork"):
                current["artwork"] = kv.split("DISPLAY_ICON_URI=", 1)[1].strip()
            elif "MEDIA_URI=" in kv:
                current["media_uri"] = kv.split("MEDIA_URI=", 1)[1].strip()
    if current:
        sessions.append(current)
    if not sessions:
        return None

    for session in sessions:
        try:
            session["artwork"] = resolve_media_artwork(bridge, session.get("artwork", ""))
        except Exception:
            session["artwork"] = ""

    preferred = (preferred_package or "").strip()
    chosen = None
    if preferred:
        for session in sessions:
            if str(session.get("package") or "").strip() == preferred:
                chosen = session
                break
    if chosen is None:
        active = [session for session in sessions if session.get("active")]
        chosen = active[0] if active else sessions[0]
    if not chosen.get("title") and not chosen.get("package"):
        return None
    chosen = dict(chosen)
    chosen["artwork"] = resolve_media_artwork(bridge, chosen.get("artwork", ""))
    chosen["sessions"] = sessions
    return chosen


def resolve_media_artwork(bridge, uri_or_path: str) -> str:
    src = str(uri_or_path or "").strip().strip('"')
    if not src:
        return ""
    if src.startswith("file://"):
        src = src[7:]
    if os.path.exists(src):
        return src

    cache_dir = os.path.join(tempfile.gettempdir(), "phonebridge-nowplaying")
    os.makedirs(cache_dir, exist_ok=True)
    ext = ".jpg"
    low = src.lower()
    for candidate in (".png", ".webp", ".jpeg", ".jpg"):
        if low.endswith(candidate):
            ext = candidate
            break
    out = os.path.join(cache_dir, f"{hashlib.sha1(src.encode('utf-8')).hexdigest()}{ext}")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    if src.startswith("/"):
        ok, _ = bridge._run("pull", src, out, timeout=8)
        if ok and os.path.exists(out) and os.path.getsize(out) > 0:
            return out

    if src.startswith("content://"):
        serial = bridge._resolve_target(allow_connect=True)
        if serial:
            ok, raw, _ = bridge._run_adb_bytes(
                "-s", serial, "shell", "content", "read", "--uri", src, timeout=8
            )
            if ok and raw:
                try:
                    with open(out, "wb") as fh:
                        fh.write(raw)
                    if os.path.getsize(out) > 0:
                        return out
                except Exception:
                    pass
    return ""


def media_play_pause(bridge):
    return bridge._run("shell", "input", "keyevent", "KEYCODE_MEDIA_PLAY_PAUSE")[0]


def media_next(bridge):
    return bridge._run("shell", "input", "keyevent", "KEYCODE_MEDIA_NEXT")[0]


def media_prev(bridge):
    return bridge._run("shell", "input", "keyevent", "KEYCODE_MEDIA_PREVIOUS")[0]


def media_stop(bridge):
    return bridge._run("shell", "input", "keyevent", "KEYCODE_MEDIA_STOP")[0]


def stop_media_app(bridge, package_name):
    if not package_name:
        return False
    return bridge._run("shell", "am", "force-stop", package_name)[0]


def launch_app(bridge, package_name: str):
    pkg = str(package_name or "").strip()
    if not pkg:
        return False
    ok, out = bridge._run(
        "shell",
        "monkey",
        "-p",
        pkg,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
        timeout=5,
    )
    text = (out or "").lower()
    return bool(ok and ("events injected: 1" in text or "monkey finished" in text))


def start_screen_recording(bridge, local_dir=None):
    if bridge._screenrecord_proc and bridge._screenrecord_proc.poll() is None:
        return None
    serial = bridge._resolve_target(allow_connect=True)
    if not serial:
        return None
    if not local_dir:
        local_dir = os.path.expanduser("~/PhoneSync/PhoneBridgeRecordings")
    os.makedirs(local_dir, exist_ok=True)
    stamp = int(time.time())
    bridge._screenrecord_remote_path = f"/sdcard/Movies/phonebridge_{stamp}.mp4"
    bridge._screenrecord_target = serial
    bridge._screenrecord_proc = subprocess.Popen(
        ["adb", "-s", serial, "shell", "screenrecord", "--time-limit", "180", bridge._screenrecord_remote_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "remote_path": bridge._screenrecord_remote_path,
        "local_dir": local_dir,
    }


def stop_screen_recording(bridge, local_dir=None):
    if not bridge._screenrecord_proc:
        return None
    if not local_dir:
        local_dir = os.path.expanduser("~/PhoneSync/PhoneBridgeRecordings")
    os.makedirs(local_dir, exist_ok=True)
    try:
        bridge._screenrecord_proc.terminate()
        bridge._screenrecord_proc.wait(timeout=6)
    except Exception:
        try:
            bridge._screenrecord_proc.kill()
        except Exception:
            pass
    bridge._screenrecord_proc = None
    if not bridge._screenrecord_remote_path:
        return None
    local_path = os.path.join(local_dir, os.path.basename(bridge._screenrecord_remote_path))
    serial = bridge._screenrecord_target or bridge._resolve_target(allow_connect=True)
    if not serial:
        bridge._screenrecord_remote_path = ""
        bridge._screenrecord_target = ""
        return None
    ok_pull, _ = bridge._run_adb("-s", serial, "pull", bridge._screenrecord_remote_path, local_path, timeout=25)
    bridge._run_adb("-s", serial, "shell", "rm", bridge._screenrecord_remote_path, timeout=6)
    bridge._screenrecord_remote_path = ""
    bridge._screenrecord_target = ""
    if ok_pull and os.path.exists(local_path):
        return local_path
    return None
