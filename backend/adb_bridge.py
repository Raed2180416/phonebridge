"""ADB bridge — correct scrcpy flags for this system"""
import subprocess
import os
import logging
import time
import re

PHONE_TARGET = "100.127.0.90:5555"
log = logging.getLogger(__name__)

class ADBBridge:
    def __init__(self, target=PHONE_TARGET):
        self.target = target
        self._screenrecord_proc = None
        self._screenrecord_remote_path = ""
        self._screenrecord_target = ""
        self._last_state = {"wifi": None, "bluetooth": None, "dnd": None}
        self._cached_devices = []
        self._cached_devices_at = 0.0
        self._last_connect_attempt_at = 0.0
        self._last_tcpip_enable_at = 0.0
        self._active_target = target

    def _run_adb(self, *args, timeout=8):
        cmd = ["adb", *args]
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

    @staticmethod
    def _parse_adb_devices(text: str):
        devices = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0].strip()
            state = parts[1].strip()
            tail = " ".join(parts[2:])
            is_tcp = ":" in serial
            transport = "wireless" if is_tcp else "usb"
            if "usb:" in tail:
                transport = "usb"
            devices.append({
                "serial": serial,
                "state": state,
                "transport": transport,
                "tail": tail,
            })
        return devices

    def _get_devices(self, force=False):
        now = time.monotonic()
        if (not force) and self._cached_devices and (now - self._cached_devices_at) < 1.0:
            return self._cached_devices
        ok, out = self._run_adb("devices", "-l", timeout=4)
        devices = self._parse_adb_devices(out if ok else "")
        self._cached_devices = devices
        self._cached_devices_at = now
        return devices

    def _configured_wireless_target(self):
        return self.target if ":" in str(self.target or "") else ""

    def _wireless_target_port(self) -> int:
        target = str(self._configured_wireless_target() or "")
        if ":" not in target:
            return 5555
        try:
            return int(target.rsplit(":", 1)[1].strip())
        except Exception:
            return 5555

    def _ensure_wireless_keepalive_from_usb(self, usb_serial: str) -> None:
        """When USB is present, keep tcpip debugging available in parallel."""
        target = self._configured_wireless_target()
        if (not target) or (not usb_serial):
            return
        devices = self._get_devices(force=False)
        for dev in devices:
            if str(dev.get("serial")) == target and str(dev.get("state")) == "device":
                return
        now = time.monotonic()
        # Throttle to avoid spamming `adb tcpip` while UI polling is active.
        if (now - self._last_tcpip_enable_at) < 600.0:
            return
        self._last_tcpip_enable_at = now
        port = str(self._wireless_target_port())
        ok_tcp, out_tcp = self._run_adb("-s", usb_serial, "tcpip", port, timeout=7)
        if not ok_tcp:
            log.warning(
                "Failed enabling adb tcpip via USB serial=%s port=%s :: %s",
                usb_serial,
                port,
                (out_tcp or "").strip(),
            )
            return
        ok_conn, out_conn = self._run_adb("connect", target, timeout=7)
        if ok_conn or ("connected" in (out_conn or "").lower()) or ("already connected" in (out_conn or "").lower()):
            self._get_devices(force=True)
            log.info("Ensured dual ADB transport (usb preferred, wireless ready): usb=%s wireless=%s", usb_serial, target)

    def _pick_connected_target(self, devices):
        connected = [d for d in devices if d.get("state") == "device"]
        usb = [d for d in connected if d.get("transport") == "usb"]
        if usb:
            return usb[0]["serial"], "usb"

        preferred = self._configured_wireless_target()
        if preferred:
            for dev in connected:
                if dev.get("serial") == preferred:
                    return preferred, "wireless"

        wireless = [d for d in connected if d.get("transport") == "wireless"]
        if wireless:
            return wireless[0]["serial"], "wireless"
        return None, None

    def _connect_wireless(self):
        target = self._configured_wireless_target()
        if not target:
            return False
        now = time.monotonic()
        if (now - self._last_connect_attempt_at) < 4.0:
            return False
        self._last_connect_attempt_at = now
        ok, out = self._run_adb("connect", target, timeout=7)
        msg = (out or "").lower()
        hinted_ok = bool(ok or ("connected" in msg) or ("already connected" in msg))
        if hinted_ok:
            # `adb connect` can report "already connected" while no usable device row exists.
            # Validate by re-reading adb device table and requiring an actual `device` state.
            devices = self._get_devices(force=True)
            for dev in devices:
                if str(dev.get("serial")) == target and str(dev.get("state")) == "device":
                    log.info("ADB wireless connect ready target=%s", target)
                    return True
            log.warning(
                "ADB wireless connect reported success but target is not usable: target=%s state_rows=%s",
                target,
                [f"{d.get('serial')}:{d.get('state')}" for d in devices],
            )
        return False

    def _resolve_target(self, allow_connect=True):
        devices = self._get_devices(force=False)
        serial, transport = self._pick_connected_target(devices)
        if serial:
            if transport == "usb":
                self._ensure_wireless_keepalive_from_usb(serial)
            self._active_target = serial
            return serial

        if allow_connect and self._connect_wireless():
            devices = self._get_devices(force=True)
            serial, transport = self._pick_connected_target(devices)
            if serial:
                if transport == "usb":
                    self._ensure_wireless_keepalive_from_usb(serial)
                self._active_target = serial
                return serial

        # Last chance: force-refresh devices and accept any connected USB/wireless target.
        devices = self._get_devices(force=True)
        serial, transport = self._pick_connected_target(devices)
        if serial:
            if transport == "usb":
                self._ensure_wireless_keepalive_from_usb(serial)
            self._active_target = serial
            return serial

        return None

    def _run(self, *args, timeout=8):
        serial = self._resolve_target(allow_connect=True)
        if not serial:
            log.warning("ADB command skipped (no connected target): adb %s", " ".join(args))
            return False, "no adb target connected"
        return self._run_adb("-s", serial, *args, timeout=timeout)

    def is_connected(self):
        ok, out = self._run("get-state", timeout=2)
        return ok and "device" in out

    def launch_scrcpy(self, mode="mirror", extra_args=None, env_overrides=None):
        """Launch scrcpy — render-driver=opengl required on this system"""
        env = os.environ.copy()
        env["WAYLAND_DISPLAY"] = "wayland-1"
        if env_overrides:
            env.update(env_overrides)

        # Higher-quality, more resilient audio defaults for network ADB links.
        audio_quality_flags = [
            "--audio-codec=aac",
            "--audio-bit-rate=256K",
            "--audio-buffer=120",
            "--audio-output-buffer=10",
        ]

        serial = self._resolve_target(allow_connect=True)
        if not serial:
            log.warning("Failed to launch scrcpy mode=%s: no adb target connected", mode)
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
                # Route ALL phone audio to PC speakers
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
        log.info("Launching scrcpy mode=%s target=%s", mode, serial)
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
        ok, out = self._run("shell", "cmd", "wifi", "status", timeout=3)
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

    def get_active_network_hint(self):
        """Best-effort network type hint from Android when KDE network type is unavailable."""
        ok, out = self._run("shell", "dumpsys", "connectivity", timeout=4)
        if ok:
            text = (out or "").lower()
            if "transport: cellular" in text or "type: mobile" in text:
                return "mobile"
            if "transport: wifi" in text or "type: wifi" in text:
                return "wifi"
        ok, out = self._run("shell", "dumpsys", "telephony.registry", timeout=4)
        if ok:
            text = (out or "").lower()
            if "nrstate=connected" in text or "mdataregstate=0" in text:
                return "mobile"
        return ""

    def set_bluetooth(self, enabled: bool):
        desired = bool(enabled)
        args = ("shell", "svc", "bluetooth", "enable" if desired else "disable")
        ok, _ = self._run(*args, timeout=15)
        # Fallback for builds where svc is flaky.
        if not ok:
            args2 = ("shell", "cmd", "bluetooth_manager", "enable" if desired else "disable")
            ok2, _ = self._run(*args2, timeout=15)
            ok = bool(ok or ok2)
        # Prime cache from observed state after command.
        end = time.time() + 3.5
        while time.time() < end:
            actual = self.get_bluetooth_enabled()
            if actual is not None and bool(actual) == desired:
                break
            time.sleep(0.25)
        if not ok:
            log.warning("Bluetooth toggle failed enabled=%s", enabled)
        return ok

    @staticmethod
    def _parse_bt_enabled(text: str):
        raw = str(text or "")
        if not raw:
            return None
        lines = raw.splitlines()
        for line in lines:
            s = line.strip().lower()
            if s.startswith("enabled:"):
                if re.search(r"\benabled:\s*true\b", s):
                    return True
                if re.search(r"\benabled:\s*false\b", s):
                    return False
        for line in lines:
            s = line.strip().lower()
            if s.startswith("state:"):
                state_txt = s.split(":", 1)[1].strip()
                if state_txt in {"on", "turning_on"}:
                    return True
                if state_txt in {"off", "turning_off", "ble_on", "ble_turning_on", "ble_turning_off"}:
                    return False
        for line in lines:
            s = line.strip().lower().replace(" ", "")
            m = re.match(r"^menable:(true|false)$", s)
            if m:
                return m.group(1) == "true"
        return None

    def get_bluetooth_enabled(self):
        ok, out = self._run("shell", "settings", "get", "global", "bluetooth_on", timeout=3)
        if ok:
            val = (out or "").strip().splitlines()
            if val:
                atom = val[-1].strip().lower()
                if atom in {"1", "true"}:
                    self._last_state["bluetooth"] = True
                    return True
                if atom in {"0", "false"}:
                    self._last_state["bluetooth"] = False
                    return False
        ok, out = self._run("shell", "dumpsys", "bluetooth_manager", timeout=4)
        if not ok:
            return self._last_state.get("bluetooth")
        parsed = self._parse_bt_enabled(out or "")
        if parsed is not None:
            self._last_state["bluetooth"] = bool(parsed)
            return bool(parsed)
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
        self._run("shell", "cmd", "window", "user-rotation", "free", timeout=3)
        ok1, _ = self._run("shell", "cmd", "window", "user-rotation", "lock", str(nxt), timeout=4)
        # Legacy fallback.
        ok2, _ = self._run("shell", "settings", "put", "system", "accelerometer_rotation", "0", timeout=4)
        ok3, _ = self._run("shell", "settings", "put", "system", "user_rotation", str(nxt), timeout=4)
        ok4, _ = self._run("shell", "wm", "user-rotation", "lock", str(nxt), timeout=4)
        ok5, _ = self._run("shell", "cmd", "display", "set-user-rotation", "lock", str(nxt), timeout=4)
        return bool(ok1 or (ok2 and ok3) or ok4 or ok5)

    def get_display_rotation(self):
        ok, out = self._run("shell", "dumpsys", "display", timeout=10)
        if not ok:
            return None
        import re
        m0 = re.search(r"mUserRotation=(\d+)", out or "")
        if m0:
            return int(m0.group(1))
        m = re.search(r"mCurrentOrientation=(\d+)", out or "")
        if m:
            return int(m.group(1))
        m2 = re.search(r"mOverrideDisplayInfo=.*?\brotation (\d)\b", out or "", re.DOTALL)
        if m2:
            return int(m2.group(1))
        m3 = re.search(r"SurfaceOrientation:\s*(\d+)", out or "")
        if m3:
            return int(m3.group(1))
        return None

    def get_now_playing(self, preferred_package: str = ""):
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
        preferred = (preferred_package or "").strip()
        chosen = None
        if preferred:
            for session in sessions:
                if str(session.get("package") or "").strip() == preferred:
                    chosen = session
                    break
        if chosen is None:
            active = [s for s in sessions if s.get("active")]
            chosen = active[0] if active else sessions[0]
        if not chosen.get("title") and not chosen.get("package"):
            return None
        chosen = dict(chosen)
        chosen["sessions"] = sessions
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

    def launch_app(self, package_name: str):
        pkg = str(package_name or "").strip()
        if not pkg:
            return False
        ok, out = self._run(
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

    def toggle_dnd(self, enable: bool):
        # Prefer "priority" mode to avoid Android's full-audio mute behavior from "none/on".
        if enable:
            modes = ("priority", "on")
        else:
            modes = ("off", "all")
        for mode in modes:
            ok, out = self._run("shell", "cmd", "notification", "set_dnd", mode, timeout=4)
            if ok and "invalid" not in (out or "").lower():
                if not enable:
                    # Best-effort restore of call stream mute state after exiting DND.
                    self._run("shell", "cmd", "audio", "adj-unmute", "0", timeout=4)
                return True
        return False

    def get_dnd_enabled(self):
        ok, out = self._run("shell", "settings", "get", "global", "zen_mode", timeout=4)
        if not ok:
            return self._last_state.get("dnd")
        text = (out or "").strip()
        if text.isdigit():
            self._last_state["dnd"] = int(text) != 0
            return self._last_state["dnd"]
        lowered = text.lower()
        if lowered in {"off", "all", "none", "priority", "alarms", "on"}:
            self._last_state["dnd"] = lowered not in {"off", "all"}
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

    def get_call_state(self) -> str:
        """Return telephony call state: idle | ringing | offhook | unknown."""
        serial = self._resolve_target(allow_connect=True)
        if not serial:
            return "unknown"
        ok, out = self._run_adb("-s", serial, "shell", "dumpsys", "telephony.registry", timeout=5)
        if not ok:
            return "unknown"
        values = []
        try:
            for raw in (out or "").splitlines():
                line = raw.strip()
                match = re.search(r"\bmCallState\s*=\s*(-?\d+)", line)
                if not match:
                    continue
                values.append(int(match.group(1)))
        except Exception:
            return "unknown"
        if not values:
            return "unknown"
        if any(v == 2 for v in values):
            return "offhook"
        if any(v == 1 for v in values):
            return "ringing"
        if all(v == 0 for v in values):
            return "idle"
        return "unknown"

    def _phone_call_active(self):
        status = self.get_call_state()
        if status == "unknown":
            return None
        return status in {"ringing", "offhook"}

    def set_call_muted(self, muted: bool):
        # Avoid muting command spam when phone is idle; unmute remains allowed as recovery.
        if muted:
            active = self._phone_call_active()
            if active is False:
                return False
        # STREAM_VOICE_CALL = 0
        telecom_flag = "true" if muted else "false"
        if muted:
            attempts = [
                ("shell", "cmd", "telecom", "mute", telecom_flag),
                ("shell", "cmd", "telecom", "set-mute", telecom_flag),
                ("shell", "cmd", "audio", "adj-mute", "0"),
            ]
        else:
            attempts = [
                ("shell", "cmd", "telecom", "mute", telecom_flag),
                ("shell", "cmd", "telecom", "set-mute", telecom_flag),
                ("shell", "cmd", "audio", "adj-unmute", "0"),
            ]
        for args in attempts:
            ok, _ = self._run(*args, timeout=4)
            if ok:
                return True
        return False

    def start_screen_recording(self, local_dir=None):
        if self._screenrecord_proc and self._screenrecord_proc.poll() is None:
            return None
        serial = self._resolve_target(allow_connect=True)
        if not serial:
            return None
        if not local_dir:
            local_dir = os.path.expanduser("~/PhoneSync/PhoneBridgeRecordings")
        os.makedirs(local_dir, exist_ok=True)
        stamp = int(time.time())
        self._screenrecord_remote_path = f"/sdcard/Movies/phonebridge_{stamp}.mp4"
        self._screenrecord_target = serial
        self._screenrecord_proc = subprocess.Popen(
            ["adb", "-s", serial, "shell", "screenrecord", "--time-limit", "180", self._screenrecord_remote_path],
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
        serial = self._screenrecord_target or self._resolve_target(allow_connect=True)
        if not serial:
            self._screenrecord_remote_path = ""
            self._screenrecord_target = ""
            return None
        ok_pull, _ = self._run_adb("-s", serial, "pull", self._screenrecord_remote_path, local_path, timeout=25)
        self._run_adb("-s", serial, "shell", "rm", self._screenrecord_remote_path, timeout=6)
        self._screenrecord_remote_path = ""
        self._screenrecord_target = ""
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
        if not self._configured_wireless_target():
            return False
        ok = self._connect_wireless()
        self._get_devices(force=True)
        return bool(ok and self._resolve_target(allow_connect=False))
