"""ADB bridge — correct scrcpy flags for this system"""
import subprocess
import os
import logging
import time

PHONE_TARGET = "100.127.0.90:5555"
log = logging.getLogger(__name__)

class ADBBridge:
    def __init__(self, target=PHONE_TARGET):
        self.target = target
        self._screenrecord_proc = None
        self._screenrecord_remote_path = ""
        self._last_state = {"wifi": None, "bluetooth": None, "dnd": None}

    def _run(self, *args, timeout=8):
        cmd = ["adb", "-s", self.target] + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                log.warning("ADB command failed: %s :: %s", " ".join(cmd), (r.stderr or r.stdout).strip())
            return r.returncode == 0, (r.stdout or r.stderr or "")
        except subprocess.TimeoutExpired:
            log.warning("ADB timeout: %s (timeout=%ss)", " ".join(cmd), timeout)
            return False, "timeout"
        except Exception as e:
            log.warning("ADB command error: %s :: %s", " ".join(cmd), e)
            return False, str(e)

    def is_connected(self):
        ok, out = self._run("get-state")
        return ok and "device" in out

    def launch_scrcpy(self, mode="mirror", extra_args=None, env_overrides=None):
        """Launch scrcpy — render-driver=opengl required on this system"""
        env = os.environ.copy()
        env["WAYLAND_DISPLAY"] = "wayland-1"
        if env_overrides:
            env.update(env_overrides)

        cmds = {
            "mirror": [
                "scrcpy", "--serial", self.target,
                "--video-bit-rate", "8M",
                "--audio-source", "output",
                "--max-size", "1920",
                "--render-driver", "opengl",
            ],
            "webcam": [
                "scrcpy", "--serial", self.target,
                "--video-source=camera",
                "--camera-facing=front",
                "--camera-size=1280x720",
                "--window-title", "PhoneBridge Webcam",
                "--render-driver", "opengl",
            ],
            "audio": [
                "scrcpy", "--serial", self.target,
                "--audio-source=voice-call",
                "--no-video",
            ],
            "audio_output": [
                # Route ALL phone audio to PC speakers
                "scrcpy", "--serial", self.target,
                "--audio-source=output",
                "--no-video",
            ],
        }
        cmd = list(cmds.get(mode, cmds["mirror"]))
        if extra_args:
            cmd.extend(list(extra_args))
        log.info("Launching scrcpy mode=%s target=%s", mode, self.target)
        try:
            proc = subprocess.Popen(cmd, env=env)
            return proc
        except Exception:
            log.exception("Failed to launch scrcpy mode=%s", mode)
            return None

    def screenshot(self):
        """Take screenshot and save to ~/Pictures/"""
        import time
        path = os.path.expanduser(f"~/Pictures/phone_{int(time.time())}.png")
        ok, _ = self._run("exec-out", "screencap", "-p")
        # Better approach: pull screencap
        self._run("shell", "screencap", "-p", "/sdcard/tmp_screenshot.png")
        ok, _ = self._run("pull", "/sdcard/tmp_screenshot.png", path)
        if ok:
            self._run("shell", "rm", "/sdcard/tmp_screenshot.png")
        return path if ok else None

    def send_text(self, text):
        """Type text on phone"""
        escaped = text.replace(" ", "%s").replace("'", "\\'")
        ok, _ = self._run("shell", "input", "text", escaped)
        return ok

    def open_hotspot_settings(self):
        ok, _ = self._run("shell", "am", "start", "-n",
                          "com.android.settings/.TetherSettings")
        return ok

    def set_hotspot(self, enabled: bool):
        """Try true hotspot commands only; never repurpose this as Wi-Fi toggle."""
        attempts = [
            ("shell", "cmd", "wifi", "start-softap" if enabled else "stop-softap"),
        ]
        for args in attempts:
            ok, _ = self._run(*args)
            if ok:
                log.info("Hotspot toggle success enabled=%s using %s", enabled, " ".join(args))
                return True
        log.warning("Hotspot toggle failed enabled=%s", enabled)
        return False

    def set_wifi(self, enabled: bool):
        ok, _ = self._run("shell", "cmd", "wifi", "set-wifi-enabled", "enabled" if enabled else "disabled")
        if not ok:
            ok, _ = self._run("shell", "svc", "wifi", "enable" if enabled else "disable")
        if ok:
            log.info("Wi-Fi toggle success enabled=%s", enabled)
        else:
            log.warning("Wi-Fi toggle failed enabled=%s", enabled)
        return ok

    def get_wifi_enabled(self):
        ok, out = self._run("shell", "cmd", "wifi", "status")
        if not ok:
            return self._last_state.get("wifi")
        text = (out or "").lower()
        if "wifi is enabled" in text:
            self._last_state["wifi"] = True
            return True
        if "wifi is disabled" in text:
            self._last_state["wifi"] = False
            return False
        return self._last_state.get("wifi")

    def set_bluetooth(self, enabled: bool):
        args = ("shell", "svc", "bluetooth", "enable" if enabled else "disable")
        ok, _ = self._run(*args, timeout=15)
        if not ok:
            log.warning("Bluetooth toggle failed enabled=%s", enabled)
        return ok

    def get_bluetooth_enabled(self):
        ok, out = self._run("shell", "dumpsys", "bluetooth_manager", timeout=6)
        if not ok:
            return self._last_state.get("bluetooth")
        text = (out or "").lower()
        if "enabled: true" in text or "state: on" in text:
            self._last_state["bluetooth"] = True
            return True
        if "enabled: false" in text or "state: off" in text:
            self._last_state["bluetooth"] = False
            return False
        return self._last_state.get("bluetooth")

    def get_contacts(self, limit=300):
        ok, out = self._run(
            "shell", "content", "query",
            "--uri", "content://contacts/phones",
            "--projection", "display_name:number",
            timeout=10,
        )
        if not ok:
            return []
        import re
        contacts = []
        for line in out.splitlines():
            m = re.search(r"display_name=(.*?), number=(.*)$", line)
            if not m:
                continue
            name = (m.group(1) or "").strip()
            number = (m.group(2) or "").strip()
            if number:
                contacts.append({"name": name or number, "phone": number})
            if len(contacts) >= limit:
                break
        return contacts

    def get_recent_calls(self, limit=30):
        ok, out = self._run(
            "shell", "content", "query",
            "--uri", "content://call_log/calls",
            "--projection", "number:name:type:date",
            timeout=10,
        )
        if not ok:
            return []
        import re
        rows = []
        for line in out.splitlines():
            m = re.search(r"number=(.*?), name=(.*?), type=(\d+), date=(\d+)", line)
            if not m:
                continue
            number = (m.group(1) or "").strip()
            name = (m.group(2) or "").strip()
            type_code = int(m.group(3))
            date_ms = int(m.group(4))
            if type_code == 1:
                event = "incoming"
            elif type_code == 2:
                event = "outgoing"
            elif type_code == 3:
                event = "missed"
            elif type_code == 6:
                event = "rejected"
            else:
                event = "other"
            rows.append({
                "number": number,
                "name": name or number,
                "event": event,
                "date_ms": date_ms,
            })
        rows.sort(key=lambda x: x["date_ms"], reverse=True)
        return rows[:limit]

    def rotate_display(self):
        current = self.get_display_rotation()
        if current is None:
            current = 0
        nxt = (current + 1) % 4
        # Preferred path on modern Android builds.
        ok1, _ = self._run("shell", "cmd", "window", "user-rotation", "lock", str(nxt), timeout=4)
        # Legacy fallback.
        ok2, _ = self._run("shell", "settings", "put", "system", "accelerometer_rotation", "0", timeout=4)
        ok3, _ = self._run("shell", "settings", "put", "system", "user_rotation", str(nxt), timeout=4)
        ok4, _ = self._run("shell", "wm", "user-rotation", "lock", str(nxt), timeout=4)
        return bool(ok1 or (ok2 and ok3) or ok4)

    def get_display_rotation(self):
        ok, out = self._run("shell", "dumpsys", "display", timeout=10)
        if not ok:
            return None
        import re
        m = re.search(r"mCurrentOrientation=(\d+)", out or "")
        if m:
            return int(m.group(1))
        m2 = re.search(r"mOverrideDisplayInfo=.*?\brotation (\d)\b", out or "", re.DOTALL)
        if m2:
            return int(m2.group(1))
        return None

    def get_now_playing(self):
        ok, out = self._run("shell", "dumpsys", "media_session", timeout=4)
        if not ok:
            return None
        import re
        sessions = []
        current = None
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
                }
                continue
            if not current:
                continue
            if "active=true" in line:
                current["active"] = True
            if "state=PlaybackState" in line:
                m = re.search(r"state=([A-Z_]+)\(", line)
                if m:
                    current["state"] = m.group(1).lower()
            if "metadata:" in line and "description=" in line:
                desc = line.split("description=", 1)[1].strip()
                parts = [p.strip() for p in desc.split(",")]
                if parts:
                    current["title"] = parts[0]
                if len(parts) > 1:
                    current["artist"] = parts[1]
                if len(parts) > 2:
                    current["album"] = parts[2]
        if current:
            sessions.append(current)
        if not sessions:
            return None
        active = [s for s in sessions if s.get("active")]
        chosen = active[0] if active else sessions[0]
        if not chosen.get("title") and not chosen.get("package"):
            return None
        return chosen

    def media_play_pause(self):
        return self._run("shell", "input", "keyevent", "KEYCODE_MEDIA_PLAY_PAUSE")[0]

    def media_next(self):
        return self._run("shell", "input", "keyevent", "KEYCODE_MEDIA_NEXT")[0]

    def media_prev(self):
        return self._run("shell", "input", "keyevent", "KEYCODE_MEDIA_PREVIOUS")[0]

    def media_stop(self):
        return self._run("shell", "input", "keyevent", "KEYCODE_MEDIA_STOP")[0]

    def stop_media_app(self, package_name):
        if not package_name:
            return False
        return self._run("shell", "am", "force-stop", package_name)[0]

    def toggle_dnd(self, enable: bool):
        mode = "on" if enable else "off"
        ok, out = self._run("shell", "cmd", "notification", "set_dnd", mode, timeout=4)
        if ok and "invalid" not in (out or "").lower():
            return True
        ok2, _ = self._run("shell", "settings", "put", "global", "zen_mode", "1" if enable else "0", timeout=4)
        return ok2

    def get_dnd_enabled(self):
        ok, out = self._run("shell", "settings", "get", "global", "zen_mode", timeout=4)
        if not ok:
            return self._last_state.get("dnd")
        text = (out or "").strip()
        if text.isdigit():
            self._last_state["dnd"] = int(text) != 0
            return self._last_state["dnd"]
        return self._last_state.get("dnd")

    def lock_phone(self):
        ok, _ = self._run("shell", "input", "keyevent", "KEYCODE_POWER")
        return ok

    def answer_call(self):
        ok, _ = self._run("shell", "input", "keyevent", "KEYCODE_HEADSETHOOK", timeout=4)
        return ok

    def end_call(self):
        ok, _ = self._run("shell", "input", "keyevent", "KEYCODE_ENDCALL", timeout=4)
        return ok

    def set_call_muted(self, muted: bool):
        # STREAM_VOICE_CALL = 0
        telecom_flag = "true" if muted else "false"
        if muted:
            attempts = [
                ("shell", "cmd", "telecom", "mute", telecom_flag),
                ("shell", "cmd", "telecom", "set-mute", telecom_flag),
                ("shell", "cmd", "audio", "adj-mute", "0"),
                ("shell", "cmd", "audio", "set-volume", "0", "0"),
                ("shell", "cmd", "audio", "set-stream-volume", "0", "0"),
                ("shell", "media", "volume", "--stream", "0", "--set", "0"),
                ("shell", "input", "keyevent", "KEYCODE_VOLUME_MUTE"),
                ("shell", "input", "keyevent", "KEYCODE_MUTE"),
            ]
        else:
            attempts = [
                ("shell", "cmd", "telecom", "mute", telecom_flag),
                ("shell", "cmd", "telecom", "set-mute", telecom_flag),
                ("shell", "cmd", "audio", "adj-unmute", "0"),
                ("shell", "media", "volume", "--stream", "0", "--set", "5"),
                ("shell", "input", "keyevent", "KEYCODE_VOLUME_MUTE"),
                ("shell", "input", "keyevent", "KEYCODE_MUTE"),
            ]
        for args in attempts:
            ok, _ = self._run(*args, timeout=4)
            if ok:
                return True
        return False

    def start_screen_recording(self, local_dir=None):
        if self._screenrecord_proc and self._screenrecord_proc.poll() is None:
            return None
        if not local_dir:
            local_dir = os.path.expanduser("~/PhoneSync/PhoneBridgeRecordings")
        os.makedirs(local_dir, exist_ok=True)
        stamp = int(time.time())
        self._screenrecord_remote_path = f"/sdcard/Movies/phonebridge_{stamp}.mp4"
        self._screenrecord_proc = subprocess.Popen(
            ["adb", "-s", self.target, "shell", "screenrecord", "--time-limit", "180", self._screenrecord_remote_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {
            "remote_path": self._screenrecord_remote_path,
            "local_dir": local_dir,
        }

    def stop_screen_recording(self, local_dir=None):
        if not self._screenrecord_proc:
            return None
        if not local_dir:
            local_dir = os.path.expanduser("~/PhoneSync/PhoneBridgeRecordings")
        os.makedirs(local_dir, exist_ok=True)
        try:
            self._screenrecord_proc.terminate()
            self._screenrecord_proc.wait(timeout=6)
        except Exception:
            try:
                self._screenrecord_proc.kill()
            except Exception:
                pass
        self._screenrecord_proc = None
        if not self._screenrecord_remote_path:
            return None
        local_path = os.path.join(local_dir, os.path.basename(self._screenrecord_remote_path))
        ok_pull, _ = self._run("pull", self._screenrecord_remote_path, local_path, timeout=25)
        self._run("shell", "rm", self._screenrecord_remote_path, timeout=6)
        self._screenrecord_remote_path = ""
        if ok_pull and os.path.exists(local_path):
            return local_path
        return None

    def get_battery_level(self):
        ok, out = self._run("shell", "dumpsys", "battery")
        if not ok:
            return -1
        for line in out.splitlines():
            if "level:" in line:
                try:
                    return int(line.split(":")[1].strip())
                except:
                    pass
        return -1

    def connect_wifi(self):
        """Reconnect ADB over Tailscale"""
        try:
            r = subprocess.run(
                ["adb", "connect", self.target],
                capture_output=True, text=True, timeout=10
            )
            return "connected" in r.stdout or "already" in r.stdout
        except:
            return False
