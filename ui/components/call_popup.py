"""Floating call popup with explicit state machine and BT route flow."""

from __future__ import annotations

import json
import subprocess
import time
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from backend import audio_route
from backend.adb_bridge import ADBBridge
from backend.linux_audio import LinuxAudio
import backend.settings_store as settings
from backend.state import state


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

    def run(self):
        mac = self._resolve_device_mac()

        self.step_update.emit(0, "pending")
        if not self._check_bt_enabled():
            self.step_update.emit(0, "fail")
            self.route_failed.emit("Bluetooth is not enabled", "Enable BT in Network or Dashboard")
            return

        connected = self._check_bt_connected(mac)
        if (not connected) and self.auto_connect and mac:
            connected = self._attempt_connect(mac)
        if not connected:
            self.step_update.emit(0, "fail")
            self.route_failed.emit("BT device not connected", "Connect device first")
            return
        self.step_update.emit(0, "ok")

        self.step_update.emit(1, "pending")
        if not self._arm_hfp_profile():
            self.step_update.emit(1, "fail")
            self.route_failed.emit("BT call profile unavailable", "Only A2DP detected")
            return
        self.step_update.emit(1, "ok")

        self.step_update.emit(2, "pending")
        audio_route.set_source("call_pc_active", True)
        result = audio_route.sync_result(
            call_retry_ms=12000,
            retry_step_ms=300,
            suspend_ui_global=True,
        )
        if (not result.ok) or (result.status != "active"):
            self.step_update.emit(2, "fail")
            audio_route.set_source("call_pc_active", False)
            audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
            reason = str(result.reason or "")
            if "profile" in reason.lower() or "a2dp" in reason.lower():
                self.route_failed.emit("BT call profile unavailable", "Only A2DP detected")
            elif "mic path" in reason.lower() or "bluez_input" in reason.lower():
                self.route_failed.emit("No BT mic input node detected", "BT mic not found via pw-dump/pactl")
            else:
                self.route_failed.emit("BT device not reachable", reason or "Device did not respond")
            return
        if not self._check_mic_node():
            self.step_update.emit(2, "fail")
            audio_route.set_source("call_pc_active", False)
            audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
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
        for c in cards:
            name = str(c.get("name", "")).lower()
            desc = str(c.get("description", "")).lower()
            if "bluez" not in name and "bluetooth" not in desc:
                continue
            if preferred and preferred not in desc and preferred not in name:
                selected.append(c)
                continue
            selected.insert(0, c)
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
        if ok and any(k in status.lower() for k in ("bluez_input.", "[bluez5]", "handsfree", "headset")):
            return True

        p_ok, sources = self._run_cmd(["pactl", "list", "sources"], timeout=3.0)
        if p_ok and any(k in sources.lower() for k in ("bluez_input.", "device.api = \"bluez5\"", "handsfree", "headset")):
            return True

        return False


class CallPopup(QWidget):
    """Singleton floating call popup that is reused across call events."""

    def __init__(self, parent_window: QWidget | None = None):
        super().__init__(None)
        self.parent_window = parent_window

        self.current_state = "ended"
        self.current_number = ""
        self.current_contact = ""
        self._is_muted = False
        self._routed_to_pc = False
        self._route_busy = False
        self._allow_close = False

        self._last_event_key = ""
        self._last_event_ts = 0.0
        self._call_session_token = 0
        self._answer_origin = "phone"
        self._auto_route_applied = False
        self._route_token_counter = 0
        self._active_route_token: tuple[int, int] | None = None
        self._route_watchdog_token: tuple[int, int] | None = None

        self._call_seconds = 0
        self._call_timer = QTimer(self)
        self._call_timer.setInterval(1000)
        self._call_timer.timeout.connect(self._tick)

        self._missed_timer = QTimer(self)
        self._missed_timer.setSingleShot(True)
        self._missed_timer.timeout.connect(self._auto_dismiss_missed)

        self._state_watch_timer = QTimer(self)
        self._state_watch_timer.setInterval(650)
        self._state_watch_timer.timeout.connect(self._poll_call_state)

        self._route_watchdog = QTimer(self)
        self._route_watchdog.setSingleShot(True)
        self._route_watchdog.timeout.connect(self._on_route_watchdog_timeout)

        self._ring_pulse_anim: QPropertyAnimation | None = None
        self._talk_pulse_anim: QPropertyAnimation | None = None
        self._bt_panel_anim: QPropertyAnimation | None = None

        self._route_worker: BTRouteWorker | None = None

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.setObjectName("CallPopup")
        self.setFixedWidth(300)

        self._build_ui()
        self._set_close_gate(False)
        self.hide()

    def update_position(self):
        if not self.parent_window:
            return
        pw = self.parent_window
        x = pw.x() + pw.width() - self.width() - 16
        y = pw.y() + 50
        self.move(x, y)

    def set_parent_window(self, parent_window: QWidget | None):
        self.parent_window = parent_window
        self.update_position()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("CallPopupCard")
        root.addWidget(self.card)

        body = QVBoxLayout(self.card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.top_bar = QWidget()
        self.top_bar.setFixedHeight(3)
        body.addWidget(self.top_bar)

        header = QHBoxLayout()
        header.setContentsMargins(12, 12, 12, 0)
        header.setSpacing(8)

        self.state_label = QLabel("CALL")
        self.state_label.setStyleSheet(
            "font-family:monospace;font-size:9px;font-weight:600;background:transparent;border:none;"
        )

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setToolTip("Cannot close during active call")
        self.close_btn.setStyleSheet(
            f"background:#212636;color:{COLOR_DIM};border:none;border-radius:9px;font-size:10px;"
        )
        self.close_btn.clicked.connect(self.try_close)

        header.addWidget(self.state_label)
        header.addStretch(1)
        header.addWidget(self.close_btn)
        body.addLayout(header)

        contact_row = QHBoxLayout()
        contact_row.setContentsMargins(12, 0, 12, 12)
        contact_row.setSpacing(10)

        self.avatar = QLabel("?")
        self.avatar.setFixedSize(42, 42)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet(
            """
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #4f8ef7,stop:1 #9b6dff);
            color:white;
            border:none;
            border-radius:21px;
            font-size:17px;
            font-weight:700;
            """
        )

        info = QVBoxLayout()
        info.setSpacing(1)
        self.name_label = QLabel("Unknown")
        self.name_label.setStyleSheet(f"color:{COLOR_TEXT};font-size:15px;font-weight:600;border:none;background:transparent;")
        self.number_label = QLabel("")
        self.number_label.setStyleSheet(f"color:{COLOR_DIM};font-size:10px;font-family:monospace;border:none;background:transparent;")
        self.timer_label = QLabel("00:00")
        self.timer_label.setStyleSheet(f"color:{COLOR_GREEN};font-size:10px;font-family:monospace;border:none;background:transparent;")
        self.timer_label.hide()
        info.addWidget(self.name_label)
        info.addWidget(self.number_label)
        info.addWidget(self.timer_label)

        contact_row.addWidget(self.avatar)
        contact_row.addLayout(info, 1)
        body.addLayout(contact_row)

        self.route_panel = QFrame()
        self.route_panel.setStyleSheet(
            f"QFrame {{background:#1a1e28;border:1px solid {COLOR_BORDER};border-radius:8px;}}"
        )
        route_layout = QVBoxLayout(self.route_panel)
        route_layout.setContentsMargins(11, 9, 11, 9)
        route_layout.setSpacing(6)

        route_title = QLabel("AUDIO ROUTE")
        route_title.setStyleSheet(f"color:{COLOR_DIM};font-size:9px;font-family:monospace;border:none;background:transparent;")
        route_layout.addWidget(route_title)

        route_row = QHBoxLayout()
        route_row.setSpacing(5)

        self.phone_option = RouteOption("📱", "Phone")
        self.phone_option.clicked.connect(self.set_route_phone)
        self.laptop_option = RouteOption("🔊", "Laptop", "BT req.")
        self.laptop_option.clicked.connect(self.set_route_laptop)

        route_row.addWidget(self.phone_option, 1)
        route_row.addWidget(self.laptop_option, 1)
        route_layout.addLayout(route_row)

        body.addWidget(self.route_panel)

        self.bt_panel = QFrame()
        self.bt_panel.setStyleSheet(
            f"QFrame {{background:#1a1e28;border:1px solid {COLOR_BORDER};border-radius:8px;}}"
        )
        self.bt_panel.setMaximumHeight(0)
        bt_layout = QVBoxLayout(self.bt_panel)
        bt_layout.setContentsMargins(0, 2, 0, 2)
        bt_layout.setSpacing(0)

        self.bt_rows = [
            BtStepRow("Checking BT connection", "bluetoothctl info ..."),
            BtStepRow("Checking HFP/HSP profile", "wpctl inspect ..."),
            BtStepRow("Detecting mic input node", "pw-dump | grep bluez ..."),
        ]
        for row in self.bt_rows:
            bt_layout.addWidget(row)

        body.addWidget(self.bt_panel)

        self.primary_row = QHBoxLayout()
        self.primary_row.setContentsMargins(12, 0, 12, 12)
        self.primary_row.setSpacing(5)

        self.primary_btn = QPushButton()
        self.primary_btn.setFixedHeight(38)
        self.secondary_btn = QPushButton()
        self.secondary_btn.setFixedHeight(38)

        self.primary_row.addWidget(self.primary_btn, 1)
        self.primary_row.addWidget(self.secondary_btn, 1)
        body.addLayout(self.primary_row)

        self.extra_row = QHBoxLayout()
        self.extra_row.setContentsMargins(12, 0, 12, 12)
        self.extra_row.setSpacing(5)

        self.reply_btn = QPushButton("Reply SMS")
        self.reply_btn.setFixedHeight(28)
        self.reply_btn.clicked.connect(self.sms_reply_diversion_flow)
        self.keypad_btn = QPushButton("Keypad")
        self.keypad_btn.setFixedHeight(28)
        self.keypad_btn.clicked.connect(self.toggle_keypad_overlay)

        self.extra_row.addWidget(self.reply_btn, 1)
        self.extra_row.addWidget(self.keypad_btn, 1)
        body.addLayout(self.extra_row)

        self.keypad_panel = QFrame()
        self.keypad_panel.setStyleSheet(
            f"QFrame {{background:#1a1e28;border:1px solid {COLOR_BORDER};border-radius:8px;}}"
        )
        self.keypad_panel.hide()
        keypad_grid = QGridLayout(self.keypad_panel)
        keypad_grid.setContentsMargins(10, 10, 10, 10)
        keypad_grid.setHorizontalSpacing(6)
        keypad_grid.setVerticalSpacing(6)

        keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"]
        for idx, key in enumerate(keys):
            btn = QPushButton(key)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                f"background:#212636;border:1px solid {COLOR_BORDER};border-radius:5px;color:{COLOR_TEXT};font-size:11px;"
            )
            btn.clicked.connect(lambda _, k=key: self._send_dtmf(k))
            keypad_grid.addWidget(btn, idx // 3, idx % 3)

        body.addWidget(self.keypad_panel)

        self.setStyleSheet(
            f"""
            QWidget#CallPopupCard {{
                background-color: {COLOR_BG};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
            """
        )

    def _button_style(self, role: str, *, active: bool = False) -> str:
        if role == "answer":
            return "background:#22c55e;color:white;border:none;border-radius:8px;font-size:12px;font-weight:500;"
        if role == "reject":
            return "background:#f05252;color:white;border:none;border-radius:8px;font-size:12px;font-weight:500;"
        if role == "neutral":
            return (
                f"background:#212636;color:#94a3b8;border:1px solid {COLOR_BORDER};"
                "border-radius:8px;font-size:12px;font-weight:500;"
            )
        if role == "mute":
            if active:
                return (
                    "background:rgba(240,82,82,0.10);color:#f05252;"
                    "border:1px solid rgba(240,82,82,0.30);border-radius:8px;font-size:12px;font-weight:500;"
                )
            return (
                f"background:#212636;color:#94a3b8;border:1px solid {COLOR_BORDER};"
                "border-radius:8px;font-size:12px;font-weight:500;"
            )
        return f"background:#212636;color:{COLOR_TEXT};border:1px solid {COLOR_BORDER};border-radius:8px;"

    def _extra_button_style(self) -> str:
        return (
            f"background:#1a1e28;color:{COLOR_DIM};border:1px solid {COLOR_BORDER};"
            "border-radius:5px;font-size:10px;font-family:monospace;"
        )

    def _set_close_gate(self, enabled: bool):
        self.close_btn.setEnabled(bool(enabled))
        effect = self.close_btn.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.close_btn)
            self.close_btn.setGraphicsEffect(effect)
        effect.setOpacity(1.0 if enabled else 0.25)

    def _start_ringing_pulse(self):
        self._stop_label_pulse()
        effect = self.top_bar.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.top_bar)
            self.top_bar.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(1000)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.45)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        anim.start()
        self._ring_pulse_anim = anim

    def _stop_ringing_pulse(self):
        if self._ring_pulse_anim is not None:
            self._ring_pulse_anim.stop()
            self._ring_pulse_anim = None
        self.top_bar.setGraphicsEffect(None)

    def _start_label_pulse(self):
        effect = self.state_label.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.state_label)
            self.state_label.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.4)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        anim.start()
        self._talk_pulse_anim = anim

    def _stop_label_pulse(self):
        if self._talk_pulse_anim is not None:
            self._talk_pulse_anim.stop()
            self._talk_pulse_anim = None
        self.state_label.setGraphicsEffect(None)

    def _set_top_bar(self, state_name: str):
        if state_name == "ringing":
            style = (
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #f97316, stop:1 #f0b429);"
            )
            label = "↓ INCOMING CALL"
            color = COLOR_ORANGE
        elif state_name == "talking":
            style = (
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #22c55e, stop:1 #4f8ef7);"
            )
            label = "● TALKING"
            color = COLOR_GREEN
        elif state_name == "missed_call":
            style = f"background:{COLOR_RED};"
            label = "↙ MISSED CALL"
            color = COLOR_RED
        else:
            style = "background:#4e5a72;"
            label = "CALL ENDED"
            color = COLOR_DIM

        self.top_bar.setStyleSheet(style)
        self.state_label.setText(label)
        self.state_label.setStyleSheet(
            f"font-family:monospace;font-size:9px;font-weight:600;color:{color};background:transparent;border:none;"
        )

    def _set_route_panel_visible(self, visible: bool):
        self.route_panel.setVisible(bool(visible))
        if not visible:
            self._animate_bt_panel(False)

    def _set_extra_buttons(self, *, show_reply: bool, show_keypad: bool):
        self.reply_btn.setVisible(show_reply)
        self.keypad_btn.setVisible(show_keypad)
        self.reply_btn.setStyleSheet(self._extra_button_style())
        self.keypad_btn.setStyleSheet(self._extra_button_style())

    def _set_primary_actions(
        self,
        left_text: str,
        left_style: str,
        left_cb: Callable[[], None],
        right_text: str,
        right_style: str,
        right_cb: Callable[[], None],
        *,
        right_checkable: bool = False,
        right_checked: bool = False,
    ):
        for btn in (self.primary_btn, self.secondary_btn):
            try:
                btn.clicked.disconnect()
            except Exception:
                pass

        self.primary_btn.setText(left_text)
        self.primary_btn.setStyleSheet(left_style)
        self.primary_btn.setCheckable(False)
        self.primary_btn.clicked.connect(left_cb)

        self.secondary_btn.setText(right_text)
        self.secondary_btn.setStyleSheet(right_style)
        self.secondary_btn.setCheckable(bool(right_checkable))
        self.secondary_btn.setChecked(bool(right_checked) if right_checkable else False)
        self.secondary_btn.clicked.connect(right_cb)

    def _animate_bt_panel(self, show: bool):
        target = 96 if show else 0
        if self._bt_panel_anim is not None:
            self._bt_panel_anim.stop()
        anim = QPropertyAnimation(self.bt_panel, b"maximumHeight", self)
        anim.setDuration(280)
        anim.setStartValue(self.bt_panel.maximumHeight())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._bt_panel_anim = anim

    def _show_popup(self):
        self.update_position()
        if self.isVisible():
            return
        self._allow_close = True
        self.show()
        self.raise_()
        self._allow_close = False

        end_pos = self.pos()
        start_pos = QPoint(end_pos.x() + 310, end_pos.y())
        self.move(start_pos)

        self._opacity_effect.setOpacity(0.0)

        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(300)
        pos_anim.setStartValue(start_pos)
        pos_anim.setEndValue(end_pos)
        pos_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        op_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        op_anim.setDuration(250)
        op_anim.setStartValue(0.0)
        op_anim.setEndValue(1.0)

        group = QParallelAnimationGroup(self)
        group.addAnimation(pos_anim)
        group.addAnimation(op_anim)
        group.start()
        self._show_group = group

    def hide_popup(self):
        if not self.isVisible():
            return

        end_x = self.x() + 280

        pos_out = QPropertyAnimation(self, b"pos", self)
        pos_out.setDuration(250)
        pos_out.setStartValue(self.pos())
        pos_out.setEndValue(QPoint(end_x, self.y()))
        pos_out.setEasingCurve(QEasingCurve.Type.InCubic)

        op_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        op_out.setDuration(220)
        op_out.setStartValue(1.0)
        op_out.setEndValue(0.0)

        group = QParallelAnimationGroup(self)
        group.addAnimation(pos_out)
        group.addAnimation(op_out)
        group.finished.connect(self._hide_in_place)
        group.start()
        self._hide_group = group

    def _close_popup_now(self):
        if self.isVisible():
            self._hide_in_place()

    def _hide_in_place(self):
        self._allow_close = True
        self.hide()
        self._allow_close = False
        self.keypad_panel.hide()
        self._animate_bt_panel(False)

    def _refresh_contact(self):
        display_name = self.current_contact or self.current_number or "Unknown"
        self.name_label.setText(display_name)
        self.number_label.setText(self.current_number if self.current_number and self.current_number != display_name else "")

        initial = "?"
        for ch in display_name:
            if ch.strip():
                initial = ch.upper()
                break
        self.avatar.setText(initial)

    def _publish_state(self, status: str, audio_target: str | None = None):
        state.set(
            "call_state",
            {
                "event": status,
                "number": self.current_number,
                "contact_name": self.current_contact,
            },
        )
        row = {
            "status": status,
            "number": self.current_number,
            "contact_name": self.current_contact,
            "audio_target": audio_target
            or ("pc" if self._routed_to_pc else ("pending_pc" if self._route_busy else "phone")),
            "updated_at": int(time.time() * 1000),
        }
        state.set("call_ui_state", row)

    def _begin_call_session(self):
        self._call_session_token += 1
        self._answer_origin = "phone"
        self._auto_route_applied = False
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = False
        self._set_phone_selected(reset_failure=True)
        self._animate_bt_panel(False)
        for row in self.bt_rows:
            row.set_state("pending")

    def _invalidate_route_callbacks(self):
        self._active_route_token = None
        self._route_watchdog_token = None
        self._route_watchdog.stop()

    def _route_callback_stale(self, token: tuple[int, int] | None) -> bool:
        if token is None:
            return True
        if token != self._active_route_token:
            return True
        if token[0] != self._call_session_token:
            return True
        if self.current_state not in {"ringing", "talking"}:
            return True
        return False

    def _start_state_watcher(self):
        if self.current_state not in {"ringing", "talking"}:
            return
        if not self._state_watch_timer.isActive():
            self._state_watch_timer.start()

    def _stop_state_watcher(self):
        if self._state_watch_timer.isActive():
            self._state_watch_timer.stop()

    def _poll_call_state(self):
        if self.current_state not in {"ringing", "talking"}:
            self._stop_state_watcher()
            return
        call_state = ADBBridge().get_call_state()
        if call_state == "unknown":
            return
        if self.current_state == "ringing":
            if call_state == "offhook":
                self._enter_talking(reset_timer=False)
                return
            if call_state == "idle":
                self._enter_ended()
                return
        if self.current_state == "talking" and call_state == "idle":
            self._enter_ended()

    def _tick(self):
        self._call_seconds += 1
        m = self._call_seconds // 60
        s = self._call_seconds % 60
        self.timer_label.setText(f"{m:02d}:{s:02d}")

    def _normalize_status(self, raw: str) -> str:
        key = (raw or "").strip().lower().replace("-", "_")
        mapping = {
            "ringing": "ringing",
            "incoming": "ringing",
            "callreceived": "ringing",
            "talking": "talking",
            "accepted": "talking",
            "active": "talking",
            "missed_call": "missed_call",
            "missedcall": "missed_call",
            "missed": "missed_call",
            "ended": "ended",
            "disconnected": "ended",
            "idle": "ended",
            "declined": "ended",
            "rejected": "ended",
        }
        return mapping.get(key, "ended")

    def handle_call_event(self, number: str, contact_name: str, status: str):
        normalized = self._normalize_status(status)
        dedupe_key = f"{normalized}|{number}|{contact_name}"
        now = time.time()
        if dedupe_key == self._last_event_key and (now - self._last_event_ts) < 0.5:
            return
        self._last_event_key = dedupe_key
        self._last_event_ts = now

        self.current_number = number or ""
        self.current_contact = contact_name or self.current_number
        self._refresh_contact()

        if normalized == "ringing":
            self._begin_call_session()
            self._enter_ringing()
        elif normalized == "talking":
            if self.current_state not in {"ringing", "talking"}:
                self._begin_call_session()
            self._enter_talking(reset_timer=False)
        elif normalized == "missed_call":
            self._enter_missed()
        else:
            self._enter_ended()

    def update_call_context(self, event: str, number: str, contact_name: str):
        self.handle_call_event(number, contact_name, event)

    def _enter_ringing(self):
        self.current_state = "ringing"
        self._is_muted = False
        self._stop_label_pulse()
        self._set_top_bar("ringing")
        self._start_ringing_pulse()
        self._teardown_route(suspend_ui_global=True)

        self.timer_label.hide()
        self._call_timer.stop()
        self._call_seconds = 0
        self.timer_label.setText("00:00")

        self._set_close_gate(False)
        self._set_route_panel_visible(True)
        self._set_primary_actions(
            "Answer",
            self._button_style("answer"),
            self.answer_call,
            "Reject",
            self._button_style("reject"),
            self.reject_call,
        )
        self._set_extra_buttons(show_reply=True, show_keypad=False)

        self._set_phone_selected(reset_failure=True)
        self._missed_timer.stop()
        self._show_popup()
        self._start_state_watcher()
        self._publish_state("ringing", "phone")

    def _enter_talking(self, *, reset_timer: bool):
        self.current_state = "talking"
        self._stop_ringing_pulse()
        self._set_top_bar("talking")
        self._start_label_pulse()

        if reset_timer:
            self._call_seconds = 0
            self.timer_label.setText("00:00")
        self.timer_label.show()
        if not self._call_timer.isActive():
            self._call_timer.start()

        self._set_close_gate(False)
        self._set_route_panel_visible(True)
        self._set_primary_actions(
            "End",
            self._button_style("reject"),
            self.end_call,
            "Mute",
            self._button_style("mute", active=self._is_muted),
            self.toggle_mute,
            right_checkable=True,
            right_checked=self._is_muted,
        )
        self._set_extra_buttons(show_reply=True, show_keypad=True)

        self._missed_timer.stop()
        self._show_popup()
        self._start_state_watcher()
        self._publish_state("talking")
        if not self._auto_route_applied:
            self._auto_route_applied = True
            if self._answer_origin == "laptop":
                QTimer.singleShot(220, self.set_route_laptop)
            else:
                self.set_route_phone()

    def _enter_missed(self):
        self.current_state = "missed_call"
        self._stop_state_watcher()
        self._invalidate_route_callbacks()
        self._teardown_route()
        self._stop_ringing_pulse()
        self._stop_label_pulse()
        self._set_top_bar("missed_call")

        self.timer_label.hide()
        self._call_timer.stop()

        self._set_close_gate(True)
        self._set_route_panel_visible(False)
        self._set_primary_actions(
            "Call Back",
            self._button_style("answer"),
            self.call_back,
            "Dismiss",
            self._button_style("neutral"),
            self.dismiss_missed,
        )
        self._set_extra_buttons(show_reply=True, show_keypad=False)

        self._publish_state("missed_call", "phone")
        self._close_popup_now()

    def _enter_ended(self):
        self.current_state = "ended"
        self._stop_state_watcher()
        self._invalidate_route_callbacks()
        self._teardown_route()
        self._stop_ringing_pulse()
        self._stop_label_pulse()
        self._set_top_bar("ended")

        self.timer_label.hide()
        self._call_timer.stop()
        self._set_close_gate(True)
        self._set_route_panel_visible(False)
        self._set_extra_buttons(show_reply=False, show_keypad=False)
        self._publish_state("ended", "phone")
        self._close_popup_now()

    def _auto_dismiss_missed(self):
        if self.current_state != "missed_call":
            return
        self.hide_popup()
        self._publish_state("ended", "phone")

    def try_close(self):
        if self.current_state in {"ringing", "talking"}:
            return
        self.hide_popup()

    def request_close(self):
        self.hide_popup()

    def answer_call(self):
        self.primary_btn.setEnabled(False)
        self.secondary_btn.setEnabled(False)
        self._answer_origin = "laptop"
        ADBBridge().answer_call()
        self._enter_talking(reset_timer=True)
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setEnabled(True)

    def reject_call(self):
        ADBBridge().end_call()
        self._enter_ended()
        try:
            from backend.ui_feedback import push_toast

            push_toast("Call rejected", "info", 1500)
        except Exception:
            pass

    def end_call(self):
        ADBBridge().end_call()
        duration = self.timer_label.text() or "00:00"
        self._enter_ended()
        try:
            from backend.ui_feedback import push_toast

            push_toast(f"Call ended · {duration}", "info", 1700)
        except Exception:
            pass

    def toggle_mute(self):
        desired = not self._is_muted
        adb_ok = ADBBridge().set_call_muted(desired)
        local_ok = False
        if bool(state.get("call_audio_active", False)):
            try:
                from backend import call_audio

                local_ok = bool(call_audio.set_input_muted(desired))
            except Exception:
                local_ok = False
        ok = bool(adb_ok or local_ok)
        if ok:
            self._is_muted = desired
        self.secondary_btn.setChecked(self._is_muted)
        self.secondary_btn.setStyleSheet(self._button_style("mute", active=self._is_muted))
        try:
            from backend.ui_feedback import push_toast

            if ok:
                push_toast("Muted" if self._is_muted else "Unmuted", "info", 1300)
            else:
                push_toast("Mute command unsupported on this Android build", "warning", 1800)
        except Exception:
            pass

    def call_back(self):
        self._missed_timer.stop()
        number = (self.current_number or "").strip()
        if number:
            ADBBridge()._run(
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.CALL",
                "-d",
                f"tel:{number}",
            )
        self._enter_ringing()

    def dismiss_missed(self):
        self._missed_timer.stop()
        self._publish_state("ended", "phone")
        self.hide_popup()

    def sms_reply_diversion_flow(self):
        self._call_timer.stop()
        if self.current_state == "talking":
            ADBBridge().end_call()
        self._teardown_route()
        state.set("sms_draft_number", self.current_number or "")
        self._publish_state("ended", "phone")

        if self.parent_window and hasattr(self.parent_window, "go_to"):
            self.parent_window.go_to("messages")

        try:
            from backend.ui_feedback import push_toast

            push_toast("Opening SMS composer…", "info", 1500)
        except Exception:
            pass
        self.hide_popup()

    def toggle_keypad_overlay(self):
        self.keypad_panel.setVisible(not self.keypad_panel.isVisible())

    def _send_dtmf(self, tone: str):
        key = str(tone).strip()
        if key == "*":
            ADBBridge()._run("shell", "input", "keyevent", "KEYCODE_STAR")
            return
        if key == "#":
            ADBBridge()._run("shell", "input", "keyevent", "KEYCODE_POUND")
            return
        if key.isdigit():
            ADBBridge()._run("shell", "input", "keyevent", f"KEYCODE_{key}")
            return

    def _teardown_route(self, *, suspend_ui_global: bool = False):
        self._route_busy = False
        self._routed_to_pc = False
        audio_route.set_source("call_pc_active", False)
        audio_route.sync_result(call_retry_ms=0, suspend_ui_global=bool(suspend_ui_global))
        self._set_phone_selected(reset_failure=False)

    def _release_bt_call_route(self) -> bool:
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            if not mgr.available():
                return False
            hints = [
                self.current_contact,
                self.current_number,
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            changed, _ = mgr.release_call_audio_route(hints, force_disconnect=True)
            return bool(changed)
        except Exception:
            return False

    def set_route_phone(self):
        self._invalidate_route_callbacks()
        self._teardown_route(suspend_ui_global=True)
        self._release_bt_call_route()
        self._animate_bt_panel(False)
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ended", "phone")

    def set_route_laptop(self):
        if self.current_state not in {"ringing", "talking"}:
            return
        if self._route_busy:
            return
        self._set_laptop_pending()
        self._animate_bt_panel(True)
        self._route_busy = True
        self._route_token_counter += 1
        token = (self._call_session_token, self._route_token_counter)
        self._active_route_token = token
        self._route_watchdog_token = token
        self._route_watchdog.start(18000)

        for row in self.bt_rows:
            row.set_state("pending")
            row.setVisible(True)

        self._route_worker = BTRouteWorker(
            preferred_name=self.current_contact or self.current_number,
            auto_connect=bool(settings.get("auto_bt_connect", True)),
            parent=self,
        )
        self._route_worker.step_update.connect(
            lambda idx, state_name, t=token: self._on_bt_step_update(t, idx, state_name)
        )
        self._route_worker.route_success.connect(
            lambda t=token: self._on_bt_route_success(t)
        )
        self._route_worker.route_failed.connect(
            lambda reason, sub_reason, t=token: self._on_bt_route_failed(t, reason, sub_reason)
        )
        self._route_worker.finished.connect(
            lambda w=self._route_worker, t=token: self._on_bt_worker_finished(w, t)
        )
        self._route_worker.start()

        state.set("call_route_status", "pending_pc")
        state.set("call_route_reason", "Preparing laptop call audio...")
        state.set("call_route_backend", "none")
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ringing", "pending_pc")

    def _set_phone_selected(self, *, reset_failure: bool):
        self.phone_option.set_mode(selected=True)
        self.laptop_option.set_mode(selected=False, failed=False, subtext="BT req." if reset_failure else None)

    def _set_laptop_pending(self):
        self.phone_option.set_mode(selected=False)
        self.laptop_option.set_mode(selected=True, failed=False, subtext="checking…")

    def _on_bt_step_update(self, token: tuple[int, int], idx: int, state_name: str):
        if self._route_callback_stale(token):
            return
        if idx < 0 or idx >= len(self.bt_rows):
            return
        self.bt_rows[idx].set_state(state_name)

    def _on_bt_route_success(self, token: tuple[int, int]):
        if self._route_callback_stale(token):
            if self.current_state not in {"ringing", "talking"}:
                audio_route.set_source("call_pc_active", False)
                audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
            return
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = True
        self.phone_option.set_mode(selected=False)
        self.laptop_option.set_mode(selected=True, subtext="ready")
        self._animate_bt_panel(False)
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "talking", "pc")
        state.set("call_route_status", "pc_active")
        state.set("call_route_reason", "Audio on laptop/PC")
        state.set("call_route_backend", "external_bt")
        try:
            from backend.ui_feedback import push_toast

            push_toast("Audio routed to laptop ✓", "success", 1500)
        except Exception:
            pass

    def _on_bt_route_failed(self, token: tuple[int, int], reason: str, sub_reason: str):
        if self._route_callback_stale(token):
            if self.current_state not in {"ringing", "talking"}:
                audio_route.set_source("call_pc_active", False)
                audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
            return
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = False
        self.phone_option.set_mode(selected=True)
        self.laptop_option.set_mode(selected=False, failed=True, subtext=sub_reason)
        self._animate_bt_panel(False)
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ringing", "phone")
        state.set("call_route_status", "pc_failed")
        state.set("call_route_reason", reason)
        state.set("call_route_backend", "none")
        try:
            from backend.ui_feedback import push_toast

            push_toast(reason, "warning", 1800)
        except Exception:
            pass

    def _on_bt_worker_finished(self, worker: BTRouteWorker | None, token: tuple[int, int]):
        if worker is None:
            return
        if (self._active_route_token is not None) and (token == self._active_route_token):
            self._route_busy = False
            self._invalidate_route_callbacks()
        if self._route_worker is worker:
            self._route_worker = None
        try:
            worker.deleteLater()
        except Exception:
            pass

    def _on_route_watchdog_timeout(self):
        token = self._route_watchdog_token
        if self._route_callback_stale(token):
            return
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = False
        audio_route.set_source("call_pc_active", False)
        audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
        self.phone_option.set_mode(selected=True)
        self.laptop_option.set_mode(selected=False, failed=True, subtext="Timed out")
        self._animate_bt_panel(False)
        state.set("call_route_status", "pc_failed")
        state.set("call_route_reason", "Laptop audio route timed out")
        state.set("call_route_backend", "none")
        self._publish_state(
            self.current_state if self.current_state in {"ringing", "talking"} else "ended",
            "phone",
        )
        try:
            from backend.ui_feedback import push_toast

            push_toast("Laptop audio route timed out", "warning", 1800)
        except Exception:
            pass

    def closeEvent(self, event):
        if (not self._allow_close) and self.current_state in {"ringing", "talking"} and event.spontaneous():
            event.ignore()
            return
        self._stop_state_watcher()
        self._route_watchdog.stop()
        self._allow_close = True
        super().closeEvent(event)
        self._allow_close = False

    def hideEvent(self, event):
        self._stop_state_watcher()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.current_state in {"ringing", "talking"}:
            event.ignore()
            return
        super().keyPressEvent(event)
