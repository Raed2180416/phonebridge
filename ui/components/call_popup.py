"""Incoming call popup — shown for all call types."""
import subprocess

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QComboBox)
from PyQt6.QtCore import Qt, QTimer
from ui.theme import TEAL, ROSE, AMBER, CYAN, TEXT_DIM, TEXT_MID
from ui.motion import slide_and_fade_in


class CallPopup(QDialog):
    """
    Shown when telephony signal fires (ringing, talking, missed_call).
    Provides: Answer/Reject/End, mute, route-to-laptop audio, quick SMS.
    """

    def __init__(self, event, number, contact_name, parent=None):
        super().__init__(parent)
        self.event = event
        self.number = number
        self.contact_name = contact_name or number
        self._scrcpy_proc = None
        self._routed_to_pc = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._build()
        self._position()
        slide_and_fade_in(self, level="rich", offset_y=8)

        if event == "missed_call":
            QTimer.singleShot(8000, self.close)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        is_ringing = self.event == "ringing"
        accent = TEAL if is_ringing else (AMBER if self.event == "talking" else ROSE)
        frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(7,12,23,252);
                border: 1px solid {accent}44;
                border-radius: 20px;
                min-width: 380px;
            }}
        """)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(22, 20, 22, 20)
        fl.setSpacing(12)

        hdr = QHBoxLayout()
        event_map = {
            "ringing": ("📞", "Incoming Call", TEAL),
            "talking": ("🔊", "Call in Progress", AMBER),
            "missed_call": ("📵", "Missed Call", ROSE),
        }
        ico, title_text, color = event_map.get(self.event, ("📞", "Call", TEAL))

        self._title = QLabel(f"{ico}  {title_text}")
        self._title.setStyleSheet(f"color:{color};font-size:13px;font-weight:600;background:transparent;border:none;")
        close = QPushButton("✕")
        close.setFixedSize(20, 20)
        close.setStyleSheet("background:transparent;color:rgba(255,255,255,0.3);border:none;font-size:13px;")
        close.clicked.connect(self.close)
        hdr.addWidget(self._title)
        hdr.addStretch()
        hdr.addWidget(close)
        fl.addLayout(hdr)

        name_lbl = QLabel(self.contact_name)
        name_lbl.setStyleSheet("color:white;font-size:20px;font-weight:700;background:transparent;border:none;")
        fl.addWidget(name_lbl)

        if self.number and self.number != self.contact_name:
            num_lbl = QLabel(self.number)
            num_lbl.setStyleSheet(f"color:{TEXT_DIM};font-size:12px;font-family:monospace;background:transparent;border:none;")
            fl.addWidget(num_lbl)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;background:transparent;border:none;")
        fl.addWidget(self._status)

        if self.event in {"ringing", "talking"}:
            route_frame = QFrame()
            route_frame.setStyleSheet("QFrame { background: rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); border-radius:10px; }")
            rl = QVBoxLayout(route_frame)
            rl.setContentsMargins(10, 8, 10, 8)
            rl.setSpacing(8)

            row1 = QHBoxLayout()
            row1.addWidget(self._small_lbl("Output"))
            self._sink_combo = QComboBox()
            self._sink_combo.setStyleSheet("QComboBox { background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1); border-radius:8px; color:white; padding:5px 8px; font-size:11px; }")
            self._sink_combo.addItem("System default", "")
            row1.addWidget(self._sink_combo)
            rl.addLayout(row1)

            self._route_btn = self._btn("Switch to Laptop Audio", CYAN, small=True)
            self._route_btn.clicked.connect(self._toggle_audio_route)
            rl.addWidget(self._route_btn)
            fl.addWidget(route_frame)

        if self.event == "ringing":
            btn_row = QHBoxLayout()
            btn_row.setSpacing(10)

            answer = self._btn("Answer", TEAL)
            answer.clicked.connect(self._answer)
            self._end_btn = self._btn("Decline", ROSE)
            self._end_btn.clicked.connect(self._decline)
            btn_row.addWidget(answer)
            btn_row.addWidget(self._end_btn)
            fl.addLayout(btn_row)

            btn_row2 = QHBoxLayout()
            btn_row2.setSpacing(10)
            mute = self._btn("Mute", AMBER, small=True)
            mute.setCheckable(True)
            mute.clicked.connect(self._toggle_mute)
            sms = self._btn("Reply SMS", TEXT_MID, small=True)
            sms.clicked.connect(self._sms_reply)
            btn_row2.addWidget(mute)
            btn_row2.addWidget(sms)
            fl.addLayout(btn_row2)

        elif self.event == "talking":
            btn_row = QHBoxLayout()
            btn_row.setSpacing(10)
            hangup = self._btn("End Call", ROSE)
            hangup.clicked.connect(self._hangup)
            mute = self._btn("Mute", AMBER, small=True)
            mute.setCheckable(True)
            mute.clicked.connect(self._toggle_mute)
            btn_row.addWidget(hangup)
            btn_row.addWidget(mute)
            fl.addLayout(btn_row)

        else:  # missed_call
            btn_row = QHBoxLayout()
            btn_row.setSpacing(10)
            callback = self._btn("Call Back", TEAL)
            callback.clicked.connect(self._call_back)
            sms = self._btn("SMS", TEXT_MID)
            sms.clicked.connect(self._sms_reply)
            btn_row.addWidget(callback)
            btn_row.addWidget(sms)
            fl.addLayout(btn_row)

        layout.addWidget(frame)
        if self.event in {"ringing", "talking"}:
            # Defer sink enumeration to avoid blocking popup render.
            QTimer.singleShot(0, self._populate_sinks)

    def _small_lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;background:transparent;border:none;")
        return l

    def _btn(self, text, color, small=False):
        b = QPushButton(text)
        pad = "6px 12px" if small else "10px 16px"
        size = "11px" if small else "13px"
        b.setStyleSheet(f"""
            QPushButton {{
                background:{color}18;border:1px solid {color}44;
                border-radius:10px;color:{color};
                padding:{pad};font-size:{size};font-weight:500;
            }}
            QPushButton:hover {{ background:{color}30;border-color:{color}77; }}
            QPushButton:checked {{ background:{color}35;border-color:{color}; }}
        """)
        return b

    def _populate_sinks(self):
        if not hasattr(self, "_sink_combo"):
            return
        self._sink_combo.clear()
        self._sink_combo.addItem("System default", "")
        try:
            r = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=0.8)
            for line in (r.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                sink = parts[1].strip()
                if sink:
                    self._sink_combo.addItem(sink, sink)
        except Exception:
            pass

    def _answer(self):
        from backend.adb_bridge import ADBBridge
        from backend.state import state
        from backend.ui_feedback import push_toast
        adb = ADBBridge()
        adb.answer_call()
        self.event = "talking"
        self._title.setText("🔊  Call in Progress")
        self._status.setText("Call answered from laptop")
        state.set("call_ui_state", {
            "status": "active",
            "number": self.number,
            "contact_name": self.contact_name,
        })
        push_toast("Call answered from laptop", "success", 1700)
        if hasattr(self, "_end_btn"):
            self._end_btn.setText("End Call")
            try:
                self._end_btn.clicked.disconnect()
            except Exception:
                pass
            self._end_btn.clicked.connect(self._hangup)
        self._toggle_audio_route(force_to_pc=True)

    def _decline(self):
        from backend.adb_bridge import ADBBridge
        from backend.state import state
        from backend.ui_feedback import push_toast
        ADBBridge().end_call()
        state.set("call_ui_state", {"status": "declined", "number": self.number, "contact_name": self.contact_name})
        push_toast("Call declined", "info", 1500)
        self.close()

    def _hangup(self):
        from backend.adb_bridge import ADBBridge
        from backend.state import state
        from backend.ui_feedback import push_toast
        ADBBridge().end_call()
        self._stop_audio_route()
        state.set("call_ui_state", {"status": "ended", "number": self.number, "contact_name": self.contact_name})
        push_toast("Call ended", "info", 1500)
        self.close()

    def _toggle_audio_route(self, force_to_pc=False):
        from backend.adb_bridge import ADBBridge

        if not force_to_pc and self._routed_to_pc:
            self._stop_audio_route()
            self._status.setText("Audio returned to phone")
            return

        selected_sink = self._sink_combo.currentData() if hasattr(self, "_sink_combo") else ""
        env_overrides = {}
        if selected_sink:
            env_overrides["PULSE_SINK"] = str(selected_sink)

        try:
            from backend.linux_audio import LinuxAudio
            la = LinuxAudio()
            cards = la.list_bt_cards()
            if cards:
                ok, msg = la.activate_hfp_for_card(cards[0]["name"])
                self._status.setText(msg if ok else f"HFP auto-setup: {msg}")
            else:
                self._status.setText("No Bluetooth call profile found; routing output only")
        except Exception:
            self._status.setText("Routing call audio to laptop")

        self._stop_audio_route()
        self._scrcpy_proc = ADBBridge().launch_scrcpy("audio", env_overrides=env_overrides)
        self._routed_to_pc = self._scrcpy_proc is not None
        from backend.ui_feedback import push_toast
        if self._routed_to_pc:
            push_toast("Using laptop audio for call", "success", 1600)
        else:
            push_toast("Call audio route failed", "warning", 1800)
        if hasattr(self, "_route_btn"):
            self._route_btn.setText("Switch to Phone Audio" if self._routed_to_pc else "Switch to Laptop Audio")

    def _stop_audio_route(self):
        if self._scrcpy_proc:
            try:
                self._scrcpy_proc.terminate()
            except Exception:
                pass
            self._scrcpy_proc = None
        self._routed_to_pc = False
        if hasattr(self, "_route_btn"):
            self._route_btn.setText("Switch to Laptop Audio")

    def _toggle_mute(self, checked):
        from backend.adb_bridge import ADBBridge
        from backend.ui_feedback import push_toast
        ok = ADBBridge().set_call_muted(bool(checked))
        self._status.setText("Muted" if (checked and ok) else "Unmuted" if (not checked and ok) else "Mute command sent")
        if checked and ok:
            push_toast("Call mute enabled", "success", 1400)
        elif (not checked) and ok:
            push_toast("Call mute disabled", "info", 1400)
        else:
            push_toast("Mute command sent (device may restrict mute)", "warning", 2000)

    def _sms_reply(self):
        from backend.state import state
        from PyQt6.QtCore import QTimer
        from backend.ui_feedback import push_toast
        state.set("sms_draft_number", self.number or "")
        parent = self.parent()
        if parent and hasattr(parent, "go_to"):
            parent.go_to("messages")
            page = parent.get_page("messages") if hasattr(parent, "get_page") else None
            if page and hasattr(page, "refresh"):
                QTimer.singleShot(50, page.refresh)
        push_toast("Ready to reply via SMS", "info", 1500)
        self.close()

    def _call_back(self):
        from backend.adb_bridge import ADBBridge
        ADBBridge()._run("shell", "am", "start", "-a",
                         "android.intent.action.CALL",
                         "-d", f"tel:{self.number}")
        self.close()

    def closeEvent(self, event):
        self._stop_audio_route()
        super().closeEvent(event)

    def _position(self):
        from PyQt6.QtWidgets import QApplication
        self.adjustSize()
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 24, geo.bottom() - self.height() - 80)
