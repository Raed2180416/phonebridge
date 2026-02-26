"""Bluetooth helper for auto-connecting the phone from laptop."""
from __future__ import annotations

import re
import shutil
import subprocess


class BluetoothManager:
    def __init__(self):
        self._has_btctl = bool(shutil.which("bluetoothctl"))

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

    def auto_connect_phone(self, preferred_names=None):
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
                            return True, f"Connected {d['name']}"
                    return False, msg

        return False, "No matching phone headset profile found among paired devices"
