"""Bluetooth helper for auto-connecting the phone from laptop."""
from __future__ import annotations

import re
import shutil
import subprocess
import logging


log = logging.getLogger(__name__)


class BluetoothManager:
    MEDIA_PROFILE_UUIDS = (
        "0000110a-0000-1000-8000-00805f9b34fb",  # Audio Source
        "0000110d-0000-1000-8000-00805f9b34fb",  # A2DP
        "0000110c-0000-1000-8000-00805f9b34fb",  # AVRCP Target
        "0000110e-0000-1000-8000-00805f9b34fb",  # AVRCP
    )
    CALL_PROFILE_UUIDS = (
        "0000111f-0000-1000-8000-00805f9b34fb",  # Handsfree AG
        "00001112-0000-1000-8000-00805f9b34fb",  # Headset AG
    )

    def __init__(self):
        self._has_btctl = bool(shutil.which("bluetoothctl"))
        self._has_busctl = bool(shutil.which("busctl"))
        self._has_wpctl = bool(shutil.which("wpctl"))

    def available(self) -> bool:
        return self._has_btctl

    def _run(self, *args, timeout=8):
        if not self._has_btctl:
            return False, ""
        try:
            r = subprocess.run(
                ["bluetoothctl", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = (r.stdout or r.stderr or "").strip()
            return r.returncode == 0, out
        except Exception:
            return False, ""

    def _busctl_profile(self, mac: str, method: str, uuid: str, timeout=5):
        if not self._has_busctl or not mac:
            return False, ""
        path = f"/org/bluez/hci0/dev_{mac.upper().replace(':', '_')}"
        try:
            r = subprocess.run(
                [
                    "busctl",
                    "--system",
                    "call",
                    "org.bluez",
                    path,
                    "org.bluez.Device1",
                    method,
                    "s",
                    uuid,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = (r.stdout or r.stderr or "").strip()
            return r.returncode == 0, out
        except Exception:
            return False, ""

    @staticmethod
    def _normalize_mac(raw: str) -> str:
        text = str(raw or "").strip().upper().replace("_", ":")
        if re.match(r"^[0-9A-F:]{17}$", text):
            return text
        return ""

    def _wpctl_media_stream_macs(self):
        if not self._has_wpctl:
            return []
        try:
            r = subprocess.run(
                ["wpctl", "status"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            return []
        if r.returncode != 0:
            return []
        rows = (r.stdout or "").splitlines()
        found = set()
        for line in rows:
            line = line.strip()
            if "bluez_input." not in line and "bluez_output." not in line:
                continue
            m = re.search(r"bluez_(?:input|output)\.([0-9A-F_]{17})", line, re.IGNORECASE)
            if not m:
                continue
            mac = self._normalize_mac(m.group(1))
            if mac:
                found.add(mac)
        return sorted(found)

    def _device_info(self, mac: str):
        ok, out = self._run("info", mac, timeout=5)
        return out if ok else ""

    @staticmethod
    def _looks_like_phone(name: str, info_text: str, preferred_tokens=None):
        name_l = str(name or "").lower()
        info_l = str(info_text or "").lower()
        tokens = [t.lower().strip() for t in (preferred_tokens or []) if str(t).strip()]
        if tokens and any(t in name_l or t in info_l for t in tokens):
            return True
        if "icon: phone" in info_l:
            return True
        if "uuid: handsfree audio gateway" in info_l or "uuid: headset ag" in info_l:
            return True
        return False

    def connected_phone_macs(self, preferred_names=None):
        devices = self.list_paired()
        if not devices:
            return []
        out = []
        seen = set()
        for d in devices:
            mac = self._normalize_mac(d.get("mac", ""))
            if not mac or mac in seen:
                continue
            info = self._device_info(mac)
            if not info:
                continue
            if "connected: yes" not in info.lower():
                continue
            if not self._looks_like_phone(d.get("name", ""), info, preferred_names):
                continue
            seen.add(mac)
            out.append(mac)
        return out

    def connect_call_profiles(self, mac: str):
        target = self._normalize_mac(mac)
        if not target:
            return False, "No valid MAC"
        ok_any = False
        for uuid in self.CALL_PROFILE_UUIDS:
            ok, _ = self._busctl_profile(target, "ConnectProfile", uuid, timeout=6)
            if ok:
                ok_any = True
        return ok_any, "Call profiles connected" if ok_any else "No call profile connected"

    def disconnect_call_profiles(self, mac: str):
        target = self._normalize_mac(mac)
        if not target:
            return False, "No valid MAC"
        if not self._has_busctl:
            return False, "busctl unavailable"
        ok_any = False
        for uuid in self.CALL_PROFILE_UUIDS:
            ok, _ = self._busctl_profile(target, "DisconnectProfile", uuid, timeout=6)
            if ok:
                ok_any = True
        return ok_any, "Call profiles disconnected" if ok_any else "No active call profile disconnected"

    def disconnect_media_profiles(self, mac: str):
        target = self._normalize_mac(mac)
        if not target:
            return False, "No valid MAC"
        if not self._has_busctl:
            return False, "busctl unavailable"
        ok_any = False
        for uuid in self.MEDIA_PROFILE_UUIDS:
            ok, _ = self._busctl_profile(target, "DisconnectProfile", uuid, timeout=6)
            if ok:
                ok_any = True
        return ok_any, "Media profiles disconnected" if ok_any else "No active media profile disconnected"

    def disconnect(self, mac: str):
        target = self._normalize_mac(mac)
        if not target:
            return False, "No valid MAC"
        ok, out = self._run("disconnect", target, timeout=12)
        text = (out or "").lower()
        if ok and ("successful" in text or "disconnected" in text):
            return True, out
        if not self.is_connected(target):
            return True, "Already disconnected"
        return False, out or "Disconnect failed"

    def enforce_call_ready_mode(self, preferred_names=None):
        preferred = [str(x) for x in (preferred_names or []) if str(x).strip()]
        candidates = set(self.connected_phone_macs(preferred))
        for mac in self._wpctl_media_stream_macs():
            info = self._device_info(mac)
            if info and self._looks_like_phone("", info, preferred):
                candidates.add(mac)
        if not candidates:
            return False, "No connected phone candidates"

        changed = False
        for mac in sorted(candidates):
            d_ok, _ = self.disconnect_media_profiles(mac)
            self.connect_call_profiles(mac)
            changed = changed or bool(d_ok)
        if changed:
            log.info("Bluetooth call-ready mode enforced for %s", ", ".join(sorted(candidates)))
        return changed, ("Media profiles disconnected" if changed else "No media profile change")

    def release_call_audio_route(self, preferred_names=None, *, force_disconnect=True):
        """Best-effort release of laptop headset role so call stays on phone."""
        preferred = [str(x) for x in (preferred_names or []) if str(x).strip()]
        candidates = set(self.connected_phone_macs(preferred))
        if not candidates:
            return False, "No connected phone candidates"
        changed = False
        for mac in sorted(candidates):
            ok_call, _ = self.disconnect_call_profiles(mac)
            if ok_call:
                changed = True
                continue
            if force_disconnect:
                ok_disc, _ = self.disconnect(mac)
                changed = changed or bool(ok_disc)
        return changed, ("Released BT call route" if changed else "No BT call route change")

    def list_paired(self):
        ok, out = self._run("devices", "Paired", timeout=5)
        if not ok or not out or "Invalid command" in out:
            ok, out = self._run("paired-devices", timeout=5)
            if not ok:
                return []
        devices = []
        for line in out.splitlines():
            m = re.match(r"^Device\s+([0-9A-F:]{17})\s+(.+)$", line.strip(), re.IGNORECASE)
            if not m:
                continue
            devices.append({"mac": m.group(1), "name": m.group(2).strip()})
        return devices

    def is_connected(self, mac):
        if not mac:
            return False
        ok, out = self._run("info", mac, timeout=5)
        if not ok:
            return False
        return "Connected: yes" in (out or "")

    def connect(self, mac):
        if not mac:
            return False, "No MAC provided"
        ok, out = self._run("connect", mac, timeout=12)
        if ok and ("Connection successful" in out or "Successful" in out):
            return True, out
        if self.is_connected(mac):
            return True, "Already connected"
        return False, out or "Connect failed"

    def trust(self, mac):
        if not mac:
            return False, "No MAC provided"
        ok, out = self._run("trust", mac, timeout=8)
        if ok and ("trust succeeded" in out.lower() or "changing" in out.lower() or "trusted: yes" in out.lower()):
            return True, out or "Trusted"
        # Some bluez versions return success text in info.
        info_ok, info = self._run("info", mac, timeout=5)
        if info_ok and "Trusted: yes" in info:
            return True, "Trusted"
        return False, out or "Trust failed"

    def _apply_post_connect_policy(self, mac: str, call_ready_only: bool):
        if not call_ready_only:
            return
        self.disconnect_media_profiles(mac)
        self.connect_call_profiles(mac)

    def auto_connect_phone(self, preferred_names=None, call_ready_only=True):
        names = [n.lower() for n in (preferred_names or []) if n]
        devices = self.list_paired()
        if not devices:
            return False, "No paired Bluetooth devices found"

        if names:
            for d in devices:
                n = (d.get("name") or "").lower()
                if any(tok in n for tok in names):
                    self.trust(d["mac"])
                    for _ in range(3):
                        ok, msg = self.connect(d["mac"])
                        if ok:
                            self._apply_post_connect_policy(d["mac"], call_ready_only=call_ready_only)
                            return True, f"Connected {d['name']}"
                    return False, msg

        for d in devices:
            ok, _ = self._run("info", d["mac"], timeout=5)
            if ok:
                text_ok, txt = self._run("info", d["mac"], timeout=5)
                if text_ok and ("UUID: Handsfree" in txt or "UUID: Headset" in txt):
                    self.trust(d["mac"])
                    for _ in range(3):
                        c_ok, msg = self.connect(d["mac"])
                        if c_ok:
                            self._apply_post_connect_policy(d["mac"], call_ready_only=call_ready_only)
                            return True, f"Connected {d['name']}"
                    return False, msg

        return False, "No matching phone headset profile found among paired devices"
