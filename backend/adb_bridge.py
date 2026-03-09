"""ADB bridge — correct scrcpy flags for this system"""
import subprocess
import os
import logging
import time
import re
import threading
import backend.settings_store as settings
from backend import adb_media
from backend import adb_telephony
from backend import runtime_config

log = logging.getLogger(__name__)
_BAD_TARGETS = {}
_BAD_TARGETS_LOCK = threading.RLock()
_GOOD_TARGETS = {}

class ADBBridge:
    def __init__(self, target=None):
        resolved_target = target
        if resolved_target is None:
            resolved_target = runtime_config.adb_target()
        self.target = str(resolved_target or "").strip()
        self._screenrecord_proc = None
        self._screenrecord_remote_path = ""
        self._screenrecord_target = ""
        self._last_state = {"wifi": None, "bluetooth": None, "dnd": None}
        self._cached_devices = []
        self._cached_devices_at = 0.0
        self._last_connect_attempt_at = 0.0
        self._last_tcpip_enable_at = 0.0
        self._active_target = ""
        self._fast_call_state_value = "unknown"
        self._fast_call_state_at = 0.0
        self._fast_call_state_fallback_at = 0.0
        self.log = log

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

    def _run_adb_bytes(self, *args, timeout=8):
        cmd = ["adb", *args]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", errors="ignore").strip()
                out = (r.stdout or b"").decode("utf-8", errors="ignore").strip()
                log.warning("ADB command failed: %s :: %s", " ".join(cmd), err or out)
            return r.returncode == 0, (r.stdout or b""), (r.stderr or b"")
        except subprocess.TimeoutExpired:
            log.warning("ADB timeout: %s (timeout=%ss)", " ".join(cmd), timeout)
            return False, b"", b"timeout"
        except Exception as e:
            log.warning("ADB command error: %s :: %s", " ".join(cmd), e)
            return False, b"", str(e).encode("utf-8", errors="ignore")

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
            fields = {}
            for token in parts[2:]:
                if ":" not in token:
                    continue
                key, value = token.split(":", 1)
                key = str(key or "").strip().lower()
                if key:
                    fields[key] = str(value or "").strip()
            is_tcp = ":" in serial
            transport = "wireless" if is_tcp else "usb"
            if "usb:" in tail:
                transport = "usb"
            devices.append({
                "serial": serial,
                "state": state,
                "transport": transport,
                "tail": tail,
                "fields": fields,
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

    def _prune_bad_targets(self):
        now = time.monotonic()
        with _BAD_TARGETS_LOCK:
            expired = [serial for serial, until in _BAD_TARGETS.items() if float(until or 0.0) <= now]
            for serial in expired:
                _BAD_TARGETS.pop(serial, None)
            fresh = [serial for serial, until in _GOOD_TARGETS.items() if float(until or 0.0) > now]
            if len(fresh) != len(_GOOD_TARGETS):
                for serial in list(_GOOD_TARGETS.keys()):
                    if serial not in fresh:
                        _GOOD_TARGETS.pop(serial, None)

    def _is_bad_target(self, serial: str) -> bool:
        token = str(serial or "").strip()
        if not token:
            return False
        self._prune_bad_targets()
        with _BAD_TARGETS_LOCK:
            return token in _BAD_TARGETS

    def _mark_target_unusable(self, serial: str, reason: str = "") -> None:
        token = str(serial or "").strip()
        if not token:
            return
        with _BAD_TARGETS_LOCK:
            _BAD_TARGETS[token] = time.monotonic() + 15.0
            _GOOD_TARGETS.pop(token, None)
        if reason:
            log.warning("ADB target temporarily blacklisted serial=%s reason=%s", token, str(reason or "").strip())

    def _mark_target_good(self, serial: str) -> None:
        token = str(serial or "").strip()
        if not token:
            return
        with _BAD_TARGETS_LOCK:
            _GOOD_TARGETS[token] = time.monotonic() + 3.0

    def _is_recent_good_target(self, serial: str) -> bool:
        token = str(serial or "").strip()
        if not token:
            return False
        self._prune_bad_targets()
        with _BAD_TARGETS_LOCK:
            return token in _GOOD_TARGETS

    @staticmethod
    def _is_unusable_target_error(serial: str, output: str) -> bool:
        token = str(serial or "").strip().lower()
        text = str(output or "").strip().lower()
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                f"device '{token}' not found",
                "device offline",
                "device unauthorized",
                "no devices/emulators found",
                "protocol fault",
                "connection reset by peer",
            )
        )

    def _run_on_serial(self, serial: str, *args, timeout=8, allow_connect_retry=True):
        resolved = str(serial or "").strip()
        if not resolved:
            return resolved, False, "no adb target connected"
        ok, out = self._run_adb("-s", resolved, *args, timeout=timeout)
        if ok or (not self._is_unusable_target_error(resolved, out)):
            return resolved, ok, out
        self._mark_target_unusable(resolved, out)
        retry_serial = self._resolve_target(allow_connect=allow_connect_retry)
        retry_token = str(retry_serial or "").strip()
        if retry_token and retry_token != resolved:
            ok2, out2 = self._run_adb("-s", retry_token, *args, timeout=timeout)
            return retry_token, ok2, out2
        return resolved, ok, out

    def _validate_target(self, serial: str) -> bool:
        token = str(serial or "").strip()
        if not token or self._is_bad_target(token):
            return False
        if self._is_recent_good_target(token):
            return True
        ok, out = self._run_adb("-s", token, "get-state", timeout=2)
        text = str(out or "").strip().lower()
        if ok and "device" in text:
            self._mark_target_good(token)
            return True
        if text:
            self._mark_target_unusable(token, out)
        return False

    def _configured_wireless_target(self):
        return self.target if ":" in str(self.target or "") else ""

    def _wireless_target_candidates(self):
        candidates = []
        explicit = str(self._configured_wireless_target() or "").strip()
        if explicit:
            candidates.append(explicit)
        phone_ip = str(runtime_config.phone_tailscale_ip() or "").strip()
        if phone_ip:
            candidates.append(f"{phone_ip}:{self._wireless_target_port()}")
        seen = set()
        ordered = []
        for candidate in candidates:
            token = str(candidate or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            ordered.append(token)
        return ordered

    def _wireless_target_port(self) -> int:
        target = str(self._configured_wireless_target() or "")
        if ":" not in target:
            return 5555
        try:
            return int(target.rsplit(":", 1)[1].strip())
        except Exception:
            return 5555

    def _device_identity_score(self, dev: dict) -> int:
        serial = str(dev.get("serial") or "").strip()
        transport = str(dev.get("transport") or "").strip()
        fields = dict(dev.get("fields") or {})
        score = 0
        explicit_target = str(self.target or "").strip()
        if explicit_target and serial == explicit_target:
            score += 5000
        wireless_candidates = set(self._wireless_target_candidates())
        if serial in wireless_candidates:
            score += 3000
        phone_ip = str(runtime_config.phone_tailscale_ip() or "").strip()
        if phone_ip and serial.startswith(phone_ip + ":"):
            score += 2000
        device_name = str(runtime_config.device_name() or "").strip().lower()
        if device_name and device_name != "phone":
            folded_hint = re.sub(r"[\s_-]+", "", device_name)
            for value in (serial, fields.get("model", ""), fields.get("device", ""), fields.get("product", "")):
                raw = str(value or "").strip().lower()
                if not raw:
                    continue
                folded_raw = re.sub(r"[\s_-]+", "", raw)
                if (device_name in raw) or (folded_hint and folded_hint in folded_raw):
                    score += 1000
                    break
        if transport == "usb":
            score += 500
        return score

    def _ensure_wireless_keepalive_from_usb(self, usb_serial: str) -> None:
        """When USB is present, keep tcpip debugging available in parallel."""
        targets = self._wireless_target_candidates()
        if (not targets) or (not usb_serial):
            return
        devices = self._get_devices(force=False)
        for target in targets:
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
            if self._is_unusable_target_error(usb_serial, out_tcp):
                self._mark_target_unusable(usb_serial, out_tcp)
            log.warning(
                "Failed enabling adb tcpip via USB serial=%s port=%s :: %s",
                usb_serial,
                port,
                (out_tcp or "").strip(),
            )
            return
        for target in targets:
            ok_conn, out_conn = self._run_adb("connect", target, timeout=7)
            if ok_conn or ("connected" in (out_conn or "").lower()) or ("already connected" in (out_conn or "").lower()):
                self._get_devices(force=True)
                log.info("Ensured dual ADB transport (usb preferred, wireless ready): usb=%s wireless=%s", usb_serial, target)
                return

    def _pick_connected_target(self, devices):
        connected = [
            d for d in devices
            if d.get("state") == "device" and not self._is_bad_target(str(d.get("serial") or ""))
        ]
        if connected:
            ranked = sorted(
                enumerate(connected),
                key=lambda item: (-self._device_identity_score(item[1]), item[0]),
            )
            chosen = ranked[0][1]
            return chosen["serial"], chosen["transport"]
        return None, None

    def _connect_wireless(self):
        targets = self._wireless_target_candidates()
        if not targets:
            return False
        now = time.monotonic()
        if (now - self._last_connect_attempt_at) < 4.0:
            return False
        self._last_connect_attempt_at = now
        for target in targets:
            ok, out = self._run_adb("connect", target, timeout=3)
            msg = (out or "").lower()
            hinted_ok = bool(ok or ("connected" in msg) or ("already connected" in msg))
            if not hinted_ok:
                continue
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
        if serial and self._validate_target(serial):
            if transport == "usb":
                self._ensure_wireless_keepalive_from_usb(serial)
            self._active_target = serial
            return serial

        if allow_connect and self._connect_wireless():
            devices = self._get_devices(force=True)
            serial, transport = self._pick_connected_target(devices)
            if serial and self._validate_target(serial):
                if transport == "usb":
                    self._ensure_wireless_keepalive_from_usb(serial)
                self._active_target = serial
                return serial

        # Last chance: force-refresh devices and accept any connected USB/wireless target.
        devices = self._get_devices(force=True)
        serial, transport = self._pick_connected_target(devices)
        if serial and self._validate_target(serial):
            if transport == "usb":
                self._ensure_wireless_keepalive_from_usb(serial)
            self._active_target = serial
            return serial

        return None

    def resolve_target(self, allow_connect=True):
        return self._resolve_target(allow_connect=allow_connect)

    def _run(self, *args, timeout=8):
        serial = self._resolve_target(allow_connect=True)
        if not serial:
            log.warning("ADB command skipped (no connected target): adb %s", " ".join(args))
            return False, "no adb target connected"
        _serial, ok, out = self._run_on_serial(serial, *args, timeout=timeout, allow_connect_retry=True)
        return ok, out

    def is_connected(self):
        ok, out = self._run("get-state", timeout=2)
        return ok and "device" in out

    def launch_scrcpy(self, mode="mirror", extra_args=None, env_overrides=None):
        return adb_media.launch_scrcpy(self, mode=mode, extra_args=extra_args, env_overrides=env_overrides)

    def screenshot(self):
        return adb_media.screenshot(self)

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

    def _is_usb_transport_connected(self) -> bool:
        devices = self._get_devices(force=False)
        for dev in devices:
            if str(dev.get("state")) == "device" and str(dev.get("transport")) == "usb":
                return True
        return False

    def is_usb_connected(self) -> bool:
        if self._is_usb_transport_connected():
            return True
        ok, out = self._run("shell", "dumpsys", "usb", timeout=4)
        if not ok:
            return False
        text = (out or "").lower()
        return ("mconnected: true" in text) or ("mconnected=true" in text) or ("connected: true" in text)

    def _set_usb_tether(self, enabled: bool):
        if enabled:
            attempts = [
                ("shell", "cmd", "connectivity", "tether", "start", "usb"),
                ("shell", "svc", "usb", "setFunctions", "rndis,adb"),
                ("shell", "svc", "usb", "setFunctions", "rndis"),
            ]
        else:
            attempts = [
                ("shell", "cmd", "connectivity", "tether", "stop", "usb"),
                ("shell", "svc", "usb", "setFunctions", "mtp,adb"),
                ("shell", "svc", "usb", "setFunctions", "adb"),
            ]
        for args in attempts:
            ok, _ = self._run(*args, timeout=6)
            if ok:
                log.info("USB tether toggle success enabled=%s using %s", enabled, " ".join(args))
                return True
        log.warning("USB tether toggle failed enabled=%s", enabled)
        return False

    def set_hotspot_smart(self, enabled: bool):
        """
        Enable connectivity sharing with priority:
        1) USB tethering when USB is connected
        2) Wi-Fi hotspot otherwise
        Never opens Android settings automatically.
        """
        desired = bool(enabled)
        if not desired:
            usb_ok = self._set_usb_tether(False)
            hs_ok = self.set_hotspot(False)
            if usb_ok or hs_ok:
                return True, "Tethering and hotspot disabled", False
            return False, "Could not disable tethering/hotspot", None

        usb_connected = self.is_usb_connected()
        if usb_connected:
            if self._set_usb_tether(True):
                return True, "USB tethering enabled (USB detected)", True
            log.warning("USB connected but USB tethering failed; trying Wi-Fi hotspot fallback")
        if self.set_hotspot(True):
            return True, ("Wi-Fi hotspot enabled" if not usb_connected else "Wi-Fi hotspot enabled (USB tethering failed)"), True
        return False, "Could not enable USB tethering or Wi-Fi hotspot", None

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

    def get_mobile_network_label(self) -> str:
        """Best-effort current mobile RAT label (e.g., 5G/LTE/IWLAN)."""
        ok, out = self._run("shell", "dumpsys", "telephony.registry", timeout=6)
        if ok:
            text = str(out or "")
            # Prefer the first (active phone ID) signal block.
            m = re.search(r"mSignalStrength=SignalStrength:\{(.+?)\}\s*$", text, re.MULTILINE)
            if m:
                block = m.group(1)
                p = re.search(r"primary=CellSignalStrength([A-Za-z0-9_]+)", block)
                if p:
                    primary = p.group(1).lower()
                    if "nr" in primary:
                        return "5G"
                    if "lte" in primary:
                        return "LTE"
                    if "wcdma" in primary:
                        return "WCDMA"
                    if "tdscdma" in primary:
                        return "TDSCDMA"
                    if "gsm" in primary:
                        return "GSM"
                    if "cdma" in primary:
                        return "CDMA"

        ok, out = self._run("shell", "getprop", "gsm.network.type", timeout=3)
        if not ok:
            return ""
        raw = str(out or "").strip()
        if not raw:
            return ""
        # Dual-SIM output is often comma-separated, pick first non-empty token.
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            token = part.upper()
            if token and token not in {"UNKNOWN", "NULL"}:
                return token
        return ""

    def get_signal_strength_level(self) -> int:
        """Best-effort cellular signal level in [0..4], or -1 if unavailable."""
        ok, out = self._run("shell", "dumpsys", "telephony.registry", timeout=6)
        if not ok:
            return -1
        text = str(out or "")
        # Prefer first (active phone ID) signal block + primary radio level.
        m = re.search(r"mSignalStrength=SignalStrength:\{(.+?)\}\s*$", text, re.MULTILINE)
        if m:
            block = m.group(1)
            p = re.search(r"primary=CellSignalStrength([A-Za-z0-9_]+)", block)
            if p:
                primary = p.group(1)
                # Match sub-block for the primary radio and extract its level.
                sub = re.search(rf"m{re.escape(primary)}=CellSignalStrength{re.escape(primary)}:\{{([^}}]+)\}}", block)
                if sub:
                    lv = re.search(r"(?:\blevel\b|\bmLevel\b)\s*=\s*(\d)", sub.group(1))
                    if lv:
                        try:
                            return max(0, min(4, int(lv.group(1))))
                        except Exception:
                            pass

        # Fallback: first encountered level token (instead of max across radios/SIMs).
        lv = re.search(r"(?:\blevel\b|\bmLevel\b)\s*=\s*(\d)", text)
        if not lv:
            return -1
        try:
            return max(0, min(4, int(lv.group(1))))
        except Exception:
            return -1

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
        return adb_telephony.get_contacts(self, limit=limit)

    def get_recent_calls(self, limit=30):
        return adb_telephony.get_recent_calls(self, limit=limit)

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
        return adb_media.get_now_playing(self, preferred_package=preferred_package)

    def _resolve_media_artwork(self, uri_or_path: str) -> str:
        return adb_media.resolve_media_artwork(self, uri_or_path)

    def media_play_pause(self):
        return adb_media.media_play_pause(self)

    def media_next(self):
        return adb_media.media_next(self)

    def media_prev(self):
        return adb_media.media_prev(self)

    def media_stop(self):
        return adb_media.media_stop(self)

    def stop_media_app(self, package_name):
        return adb_media.stop_media_app(self, package_name)

    def launch_app(self, package_name: str):
        return adb_media.launch_app(self, package_name)

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
        return adb_telephony.answer_call(self)

    def end_call(self):
        return adb_telephony.end_call(self)

    def get_call_state_fast(self) -> str:
        return adb_telephony.get_call_state_fast(self)

    def get_call_state(self) -> str:
        return adb_telephony.get_call_state(self)

    def _phone_call_active(self):
        return adb_telephony.phone_call_active(self)

    def set_call_muted(self, muted: bool):
        return adb_telephony.set_call_muted(self, muted)

    def start_screen_recording(self, local_dir=None):
        return adb_media.start_screen_recording(self, local_dir=local_dir)

    def stop_screen_recording(self, local_dir=None):
        return adb_media.stop_screen_recording(self, local_dir=local_dir)

    def get_battery_level(self):
        ok, out = self._run("shell", "dumpsys", "battery")
        if not ok:
            return -1
        for line in out.splitlines():
            if "level:" in line:
                try:
                    return int(line.split(":")[1].strip())
                except Exception:
                    log.debug("Failed parsing battery level line=%s", line, exc_info=True)
        return -1

    def connect_wifi(self):
        """Reconnect ADB over Tailscale"""
        if not self._configured_wireless_target():
            return False
        ok = self._connect_wireless()
        self._get_devices(force=True)
        return bool(ok and self._resolve_target(allow_connect=False))
