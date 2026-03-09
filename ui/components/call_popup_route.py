"""Route-worker helpers for the call popup."""

from __future__ import annotations

import json
import subprocess
import time

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from backend import audio_route
from backend.linux_audio import LinuxAudio


COLOR_BG = "#13161d"
COLOR_BORDER = "#252b3b"
COLOR_TEXT = "#dde3f0"
COLOR_DIM = "#4e5a72"
COLOR_ORANGE = "#f97316"
COLOR_YELLOW = "#f0b429"
COLOR_GREEN = "#22c55e"
COLOR_BLUE = "#4f8ef7"
COLOR_RED = "#f05252"
COLOR_VIOLET = "#a78bfa"

ROUTE_UNSELECTED = """
background-color: transparent;
border: 1px solid #252b3b;
border-radius: 6px;
color: #4e5a72;
"""

ROUTE_SELECTED = """
background-color: rgba(79,142,247,0.10);
border: 1px solid #4f8ef7;
border-radius: 6px;
color: #7aaaf9;
"""

ROUTE_FAILED = """
background-color: rgba(240,82,82,0.08);
border: 1px solid rgba(240,82,82,0.4);
border-radius: 6px;
color: #f05252;
"""


class BtStepRow(QWidget):
    """One step row in the BT negotiation panel."""

    def __init__(self, title: str, sub: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._base_title = title
        self.setFixedHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        self.icon = QLabel("○")
        self.icon.setFixedWidth(13)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.title = QLabel(title)
        self.title.setStyleSheet(f"color:{COLOR_DIM};font-size:10px;border:none;background:transparent;")
        self.sub = QLabel(sub)
        self.sub.setStyleSheet(f"color:{COLOR_DIM};font-size:9px;border:none;background:transparent;")
        text_col.addWidget(self.title)
        text_col.addWidget(self.sub)

        layout.addWidget(self.icon)
        layout.addLayout(text_col, 1)

        self.set_state("pending")

    def set_state(self, state_name: str, text: str | None = None, sub: str | None = None):
        if state_name == "ok":
            color = COLOR_GREEN
            icon = "✓"
        elif state_name == "fail":
            color = COLOR_RED
            icon = "✕"
        else:
            color = COLOR_YELLOW
            icon = "○"
        self.icon.setText(icon)
        self.icon.setStyleSheet(
            f"color:{color};font-size:12px;font-weight:700;border:none;background:transparent;"
        )
        self.title.setText(text or self._base_title)
        self.title.setStyleSheet(f"color:{color};font-size:10px;border:none;background:transparent;")
        if sub is not None:
            self.sub.setText(sub)


class RouteOption(QFrame):
    """Clickable route option tile."""

    clicked = pyqtSignal()

    def __init__(self, icon: str, title: str, sub: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._title_text = title
        self._sub_default = sub
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(1)

        self.icon = QLabel(icon)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("border:none;background:transparent;font-size:15px;")

        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet("border:none;background:transparent;font-size:10px;font-weight:600;")

        self.sub = QLabel(sub)
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setStyleSheet(f"border:none;background:transparent;font-size:8px;color:{COLOR_DIM};")

        layout.addWidget(self.icon)
        layout.addWidget(self.title)
        layout.addWidget(self.sub)

        self.set_mode(selected=False)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_mode(self, *, selected: bool = False, failed: bool = False, subtext: str | None = None):
        if failed:
            style = ROUTE_FAILED
            title_color = COLOR_RED
        elif selected:
            style = ROUTE_SELECTED
            title_color = "#7aaaf9"
        else:
            style = ROUTE_UNSELECTED
            title_color = COLOR_DIM

        self.setStyleSheet(f"QFrame {{{style}}}")
        self.title.setStyleSheet(
            f"border:none;background:transparent;font-size:10px;font-weight:600;color:{title_color};"
        )
        self.icon.setStyleSheet(f"border:none;background:transparent;font-size:15px;color:{title_color};")

        final_sub = self._sub_default if subtext is None else str(subtext)
        self.sub.setText(final_sub)
        self.sub.setStyleSheet(
            f"border:none;background:transparent;font-size:8px;color:{title_color if failed else COLOR_DIM};"
        )


class BTRouteWorker(QThread):
    """Asynchronous BT call-route checks and activation."""

    step_update = pyqtSignal(int, str)
    route_success = pyqtSignal()
    route_failed = pyqtSignal(str, str)

    def __init__(self, preferred_name: str, auto_connect: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.preferred_name = preferred_name or ""
        self.auto_connect = bool(auto_connect)

    def _cancel_requested(self) -> bool:
        return bool(self.isInterruptionRequested())

    def _cancel_route(self) -> None:
        audio_route.set_source("call_pc_active", False)
        try:
            audio_route.sync_result(
                call_retry_ms=0,
                suspend_ui_global=True,
            )
        except Exception:
            pass

    def run(self):
        mac = self._resolve_device_mac()
        hfp_ready = False

        if self._cancel_requested():
            self._cancel_route()
            return
        self.step_update.emit(0, "pending")
        if not self._check_bt_enabled():
            self.step_update.emit(0, "fail")
            self.route_failed.emit("Bluetooth is not enabled", "Enable BT in Network or Dashboard")
            return
        if self._cancel_requested():
            self._cancel_route()
            return

        connected = self._check_bt_connected(mac)
        if (not connected) and self.auto_connect and mac:
            connected = self._attempt_connect(mac)
        if not connected:
            self.step_update.emit(0, "fail")
            self.route_failed.emit("BT device not connected", "Connect device first")
            return
        self.step_update.emit(0, "ok")

        if self._cancel_requested():
            self._cancel_route()
            return
        self.step_update.emit(1, "pending")
        hfp_ready = self._arm_hfp_profile()
        if hfp_ready:
            self.step_update.emit(1, "ok")
        else:
            self.step_update.emit(1, "pending")

        if self._cancel_requested():
            self._cancel_route()
            return
        self.step_update.emit(2, "pending")
        audio_route.set_source("call_pc_active", True)
        result = audio_route.sync_result(
            call_retry_ms=12_000,
            retry_step_ms=300,
            suspend_ui_global=True,
            cancel_check=self._cancel_requested,
        )
        if result.status == "cancelled":
            self._cancel_route()
            return
        if (not result.ok) or (result.status != "active"):
            self.step_update.emit(2, "fail")
            self._cancel_route()
            reason = str(result.reason or "")
            if ("profile" in reason.lower() or "a2dp" in reason.lower()) and (not hfp_ready):
                self.route_failed.emit("BT call profile unavailable", "Only A2DP detected")
            elif "mic path" in reason.lower() or "bluez_input" in reason.lower():
                self.route_failed.emit("No BT mic input node detected", "BT mic not found via pw-dump/pactl")
            else:
                self.route_failed.emit("BT device not reachable", reason or "Device did not respond")
            return
        if self._cancel_requested():
            self._cancel_route()
            return
        if audio_route._bt_call_mic_path_active():
            self.step_update.emit(2, "ok")
            self.route_success.emit()
            return
        if not self._check_mic_node():
            self.step_update.emit(2, "fail")
            self._cancel_route()
            self.route_failed.emit("No BT mic input node detected", "BT mic not found via pw-dump/pactl")
            return
        self.step_update.emit(2, "ok")
        self.route_success.emit()

    def _run_cmd(self, args: list[str], timeout: float = 3.0) -> tuple[bool, str]:
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            out = (proc.stdout or proc.stderr or "").strip()
            return proc.returncode == 0, out
        except Exception:
            return False, ""

    def _resolve_device_mac(self) -> str:
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            hints = [self.preferred_name, "nothing", "phone", "a059"]
            hints = [h for h in hints if h]
            connected = mgr.connected_phone_macs(hints)
            if connected:
                return connected[0]
            for dev in mgr.list_paired():
                name = str(dev.get("name") or "").lower()
                if any(h.lower() in name for h in hints):
                    return str(dev.get("mac") or "")
        except Exception:
            return ""
        return ""

    def _check_bt_enabled(self) -> bool:
        ok, out = self._run_cmd(["bluetoothctl", "show"], timeout=3.0)
        if not ok:
            return False
        return "powered: yes" in out.lower()

    def _check_bt_connected(self, mac: str) -> bool:
        if not mac:
            ok, out = self._run_cmd(["bluetoothctl", "devices", "Connected"], timeout=3.0)
            return bool(ok and "Device" in out)
        ok, out = self._run_cmd(["bluetoothctl", "info", mac], timeout=3.0)
        return bool(ok and "Connected: yes" in out)

    def _attempt_connect(self, mac: str) -> bool:
        self._run_cmd(["bluetoothctl", "connect", mac], timeout=8.0)
        time.sleep(1.4)
        return self._check_bt_connected(mac)

    def _arm_hfp_profile(self) -> bool:
        audio = LinuxAudio()
        if not audio.available():
            return self._check_hfp_profile()

        cards = audio.list_bt_cards()
        if not cards:
            return False

        preferred = (self.preferred_name or "").lower()
        selected = []
        for card in cards:
            name = str(card.get("name", "")).lower()
            desc = str(card.get("description", "")).lower()
            if "bluez" not in name and "bluetooth" not in desc:
                continue
            if preferred and preferred not in desc and preferred not in name:
                selected.append(card)
                continue
            selected.insert(0, card)
        if not selected:
            selected = cards

        for card in selected:
            card_name = str(card.get("name") or "")
            if not card_name:
                continue
            ok, _ = audio.activate_hfp_for_card(card_name)
            if ok:
                return True
        return self._check_hfp_profile()

    def _check_hfp_profile(self) -> bool:
        ok, status = self._run_cmd(["wpctl", "status"], timeout=3.0)
        if ok:
            ids: list[str] = []
            for line in status.splitlines():
                if "[bluez5]" not in line:
                    continue
                part = line.strip().split(".", 1)[0].strip(" *│├└")
                if part.isdigit():
                    ids.append(part)
            for dev_id in ids:
                i_ok, txt = self._run_cmd(["wpctl", "inspect", dev_id], timeout=3.0)
                if not i_ok:
                    continue
                low = txt.lower()
                if any(k in low for k in ("hfp_hf", "hsp_hs", "headset-head-unit", "handsfree", "headset")):
                    return True

        p_ok, cards = self._run_cmd(["pactl", "list", "cards"], timeout=3.0)
        if p_ok:
            low = cards.lower()
            return ("bluez" in low) and any(k in low for k in ("hfp", "hsp", "headset", "handsfree"))
        return False

    def _check_mic_node(self) -> bool:
        ok, data = self._run_cmd(["pw-dump"], timeout=4.0)
        if ok and data:
            try:
                rows = json.loads(data)
                for row in rows:
                    if "Node" not in str(row.get("type", "")):
                        continue
                    props = (((row.get("info") or {}).get("props")) or {})
                    media_class = str(props.get("media.class", ""))
                    node_name = str(props.get("node.name", "")).lower()
                    is_bt_source = (
                        "bluez_input." in node_name
                        or str(props.get("device.api", "")).lower() == "bluez5"
                        or bool(props.get("api.bluez5.address"))
                        or str(props.get("device.bus", "")).lower() == "bluetooth"
                    )
                    if media_class == "Audio/Source" and is_bt_source:
                        return True
            except Exception:
                pass

        ok, status = self._run_cmd(["wpctl", "status"], timeout=3.0)
        if ok and audio_route._bt_call_mic_path_active():
            return True

        p_ok, sources = self._run_cmd(["pactl", "list", "sources"], timeout=3.0)
        if p_ok and any(k in sources.lower() for k in ("bluez_input.", "device.api = \"bluez5\"", "handsfree", "headset")):
            return True

        return False
