"""Calls page — place calls, contacts, call history"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame,
                              QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
import subprocess
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      divider, TEAL, CYAN, VIOLET, ROSE, AMBER, TEXT,
                      TEXT_DIM, TEXT_MID, BORDER)
from backend.kdeconnect import KDEConnect
from backend.adb_bridge import ADBBridge
from backend.ui_feedback import push_toast
from backend import audio_route
import backend.settings_store as settings
from backend.state import state
import time


class CallsHistoryWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, target, limit=40):
        super().__init__()
        self._target = target
        self._limit = limit

    def run(self):
        rows = ADBBridge(self._target).get_recent_calls(limit=self._limit)
        self.done.emit(rows or [])


class ContactsWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, target, limit=500):
        super().__init__()
        self._target = target
        self._limit = limit

    def run(self):
        rows = ADBBridge(self._target).get_contacts(limit=self._limit)
        self.done.emit(rows or [])


class CallRouteWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, preferred_name: str = ""):
        super().__init__()
        self._preferred_name = str(preferred_name or "")

    def _prepare_bt_call_route(self):
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            if not mgr.available():
                return
            hints = [
                self._preferred_name,
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            connected = mgr.connected_phone_macs(hints)
            if (not connected) and bool(settings.get("auto_bt_connect", True)):
                mgr.auto_connect_phone(hints, call_ready_only=False)
                connected = mgr.connected_phone_macs(hints)
            for mac in connected:
                mgr.connect_call_profiles(mac)
        except Exception:
            return

    def run(self):
        self._prepare_bt_call_route()
        audio_route.set_source("call_pc_active", True)
        result = audio_route.sync_result(
            call_retry_ms=8000,
            retry_step_ms=300,
            suspend_ui_global=True,
        )
        if not result.ok:
            audio_route.set_source("call_pc_active", False)
            audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
        self.done.emit(result)


class CallsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc  = KDEConnect()
        self.adb = ADBBridge()
        self._call_history = []
        self._contacts = []
        self._last_history_refresh = 0.0
        self._call_muted = False
        self._history_busy = False
        self._history_worker = None
        self._contacts_busy = False
        self._contacts_worker = None
        self._call_route_worker = None
        self._call_route_context = {}
        self._call_started = False
        self._pc_route_requested = False
        self._pc_route_retry_after_connect_done = False
        self._build()
        from backend.state import state
        state.subscribe("call_audio_active", self._on_call_audio_state_changed)
        state.subscribe("call_route_status", self._on_call_route_status_changed)
        state.subscribe("call_ui_state", self._on_call_ui_state_changed)
        self._on_call_route_status_changed(state.get("call_route_status", "phone"))
        self.refresh()

    def _on_call_audio_state_changed(self, active):
        if hasattr(self, "_route_live_btn"):
            self._route_live_btn.setProperty("active", bool(active))
            self._route_live_btn.style().unpolish(self._route_live_btn)
            self._route_live_btn.style().polish(self._route_live_btn)
        self._update_live_controls()

    def _on_call_route_status_changed(self, status):
        if not hasattr(self, "_call_route_hint"):
            return
        s = str(status or "phone")
        if s == "pending_pc":
            self._call_route_hint.setText("Call Audio: Preparing laptop route...")
            self._call_route_hint.setStyleSheet(f"color:{AMBER};font-size:10px;background:transparent;border:none;")
            self._update_live_controls()
            return
        if s == "pc_active":
            self._call_route_hint.setText("Call Audio: Laptop/PC")
            self._call_route_hint.setStyleSheet(f"color:{TEAL};font-size:10px;background:transparent;border:none;")
            self._update_live_controls()
            return
        if s == "pc_speaker_only":
            self._call_route_hint.setText("Call Audio: Laptop/PC output (phone mic)")
            self._call_route_hint.setStyleSheet(f"color:{AMBER};font-size:10px;background:transparent;border:none;")
            self._update_live_controls()
            return
        if s == "pc_failed":
            self._call_route_hint.setText("Call Audio: Phone (PC route failed)")
            self._call_route_hint.setStyleSheet(f"color:{ROSE};font-size:10px;background:transparent;border:none;")
            self._update_live_controls()
            return
        self._call_route_hint.setText("Call Audio: Phone")
        self._call_route_hint.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;background:transparent;border:none;")
        self._update_live_controls()

    def _on_call_ui_state_changed(self, payload):
        if not hasattr(self, "_call_state_hint"):
            return
        row = payload or {}
        raw_status = str(row.get("status") or "idle").strip().lower()
        status = raw_status.replace("_", " ").title()
        number = str(row.get("number") or "").strip()
        name = str(row.get("contact_name") or "").strip()
        who = name or number
        self._call_state_hint.setText(f"Call State: {status}{' · ' + who if who else ''}")
        self._call_started = raw_status in {"talking", "active"}
        if raw_status in {"ended", "disconnected", "idle", "missed_call"}:
            self._pc_route_requested = False
            self._pc_route_retry_after_connect_done = False
            if self._call_muted:
                self._call_muted = False
                self.adb.set_call_muted(False)
                self._set_local_mic_mute(False)
                if hasattr(self, "_mute_btn"):
                    self._mute_btn.setText("Mute")
        if (
            self._call_started
            and self._pc_route_requested
            and (self._call_route_worker is None)
            and not self._pc_route_retry_after_connect_done
            and (not bool(state.get("call_audio_active", False)))
        ):
            self._pc_route_retry_after_connect_done = True
            state.set("call_route_status", "pending_pc")
            state.set("call_route_reason", "Preparing laptop call audio...")
            state.set("call_route_backend", "none")
            self._start_call_route_attempt(number, who or number, intent="outbound_auto")
        self._update_live_controls()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(14)
        layout.addWidget(lbl("Calls", 22, bold=True))
        guide = card_frame()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.setSpacing(4)
        gl.addWidget(section_label("Flow"))
        gl.addWidget(lbl("1) Dial or choose contact  2) Answer/End from laptop  3) Use laptop audio if needed", 11, TEXT_DIM))
        layout.addWidget(guide)

        # ── Dialpad ──────────────────────────────────────────────
        dial_frame = card_frame()
        dl = QVBoxLayout(dial_frame)
        dl.setContentsMargins(20,16,20,18)
        dl.setSpacing(10)
        dl.addWidget(section_label("Dial"))

        self._dial_input = input_field("Phone number or contact…")
        dl.addWidget(self._dial_input)

        # Dialpad grid
        pad = QGridLayout()
        pad.setSpacing(8)
        digits = [
            ("1",""),("2",""),("3",""),
            ("4",""),("5",""),("6",""),
            ("7",""),("8",""),("9",""),
            ("*",""),("0","+"),("#",""),
        ]
        for i, (d, sub) in enumerate(digits):
            btn = QPushButton()
            btn.setFixedSize(64,48)
            main_lbl = d
            sub_lbl  = f"\n{sub}" if sub else ""
            btn.setText(f"{main_lbl}{sub_lbl}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.04);
                    border: 1px solid rgba(255,255,255,0.07);
                    border-radius: 12px;
                    color: white;
                    font-size: 16px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background: rgba(167,139,250,0.08);
                    border-color: rgba(167,139,250,0.2);
                }}
                QPushButton:pressed {{
                    background: rgba(167,139,250,0.15);
                }}
            """)
            digit = d
            btn.clicked.connect(lambda _, x=digit: self._append_digit(x))
            pad.addWidget(btn, i//3, i%3)

        dl.addLayout(pad)

        call_row = QHBoxLayout()
        call_row.setSpacing(8)
        call_btn = action_btn("Call", TEAL)
        call_btn.clicked.connect(self._place_call)
        clear_btn = action_btn("⌫", ROSE)
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(lambda: self._dial_input.setText(
            self._dial_input.text()[:-1]))
        call_row.addWidget(call_btn)
        call_row.addWidget(clear_btn)
        dl.addLayout(call_row)

        live_row = QHBoxLayout()
        live_row.setSpacing(8)
        end_btn = action_btn("End", ROSE)
        end_btn.clicked.connect(self._end_call)
        self._mute_btn = action_btn("Mute", AMBER)
        self._mute_btn.clicked.connect(self._toggle_live_mute)
        self._route_live_btn = action_btn("Switch to Phone Audio", CYAN)
        self._route_live_btn.clicked.connect(self._toggle_call_audio_route)
        for btn in (end_btn, self._mute_btn, self._route_live_btn):
            live_row.addWidget(btn)
        dl.addLayout(live_row)
        self._call_route_hint = lbl("Call Audio: Phone", 10, TEXT_DIM)
        self._call_state_hint = lbl("Call State: Idle", 10, TEXT_DIM)
        dl.addWidget(self._call_route_hint)
        dl.addWidget(self._call_state_hint)
        self._end_btn = end_btn
        self._update_live_controls()
        layout.addWidget(dial_frame)

        # ── Contacts ──────────────────────────────────────────────
        contacts_frame = card_frame()
        cl = QVBoxLayout(contacts_frame)
        cl.setContentsMargins(20,16,20,16)
        cl.setSpacing(10)

        chdr = QHBoxLayout()
        self._contacts_toggle = QPushButton("▸")
        self._contacts_toggle.setFixedSize(24, 24)
        self._contacts_toggle.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;color:{TEXT_DIM};font-size:14px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        self._contacts_toggle.clicked.connect(self._toggle_contacts_section)
        chdr.addWidget(self._contacts_toggle)
        chdr.addWidget(section_label("Contacts"))
        chdr.addStretch()
        sync_btn = QPushButton("Sync ↻")
        sync_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;
                color:{TEXT_DIM};font-size:11px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        sync_btn.clicked.connect(self._sync_contacts)
        chdr.addWidget(sync_btn)
        cl.addLayout(chdr)

        self._contacts_body = QWidget()
        self._contacts_body_layout = QVBoxLayout(self._contacts_body)
        self._contacts_body_layout.setContentsMargins(0, 0, 0, 0)
        self._contacts_body_layout.setSpacing(8)

        self._contact_search = input_field("Search contacts…")
        self._contact_search.textChanged.connect(self._filter_contacts)
        self._contacts_body_layout.addWidget(self._contact_search)

        self._contacts_list = QVBoxLayout()
        self._contacts_list.setSpacing(4)
        self._contacts_body_layout.addLayout(self._contacts_list)

        load_btn = action_btn("Load Contacts", CYAN)
        load_btn.clicked.connect(self._load_contacts)
        self._contacts_body_layout.addWidget(load_btn)
        self._contacts_body.setVisible(False)
        cl.addWidget(self._contacts_body)
        layout.addWidget(contacts_frame)

        # ── Call History ──────────────────────────────────────────
        hist_frame = card_frame()
        hl = QVBoxLayout(hist_frame)
        hl.setContentsMargins(20,16,20,16)
        hl.setSpacing(8)
        hhdr = QHBoxLayout()
        self._history_toggle = QPushButton("▸")
        self._history_toggle.setFixedSize(24, 24)
        self._history_toggle.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;color:{TEXT_DIM};font-size:14px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        self._history_toggle.clicked.connect(self._toggle_history_section)
        hhdr.addWidget(self._history_toggle)
        hhdr.addWidget(section_label("Recent Calls"))
        hhdr.addStretch()
        hl.addLayout(hhdr)

        self._history_body = QWidget()
        self._history_body_layout = QVBoxLayout(self._history_body)
        self._history_body_layout.setContentsMargins(0, 0, 0, 0)
        self._history_body_layout.setSpacing(6)
        self._history_list = QVBoxLayout()
        self._history_list.setSpacing(4)
        self._history_body_layout.addLayout(self._history_list)
        self._empty_hist = lbl("No recent calls", 12, TEXT_DIM)
        self._history_list.addWidget(self._empty_hist)
        self._history_body.setVisible(False)
        hl.addWidget(self._history_body)
        layout.addWidget(hist_frame)
        layout.addStretch()

    def _append_digit(self, digit):
        self._dial_input.setText(self._dial_input.text() + digit)

    def _place_call(self):
        number = self._dial_input.text().strip()
        if not number:
            return
        self._pc_route_requested = True
        self._pc_route_retry_after_connect_done = False
        state.set("outbound_call_origin", {
            "source": "calls_page",
            "number": number,
            "ts_ms": int(time.time() * 1000),
            "active": True,
        })
        self.adb._run("shell", "am", "start",
                       "-a", "android.intent.action.CALL",
                       "-d", f"tel:{number}")
        state.set("call_route_status", "phone")
        state.set("call_route_reason", "Call audio on phone until call is active")
        state.set("call_route_backend", "none")
        state.set("call_ui_state", {
            "status": "dialing",
            "number": number,
            "contact_name": number,
            "audio_target": "pending_pc",
            "updated_at": int(time.time() * 1000),
        })
        self._update_live_controls()

    def _sync_contacts(self):
        self.kc.sync_contacts()
        QTimer.singleShot(2000, self._load_contacts)

    def _load_contacts(self):
        contacts = self.kc.get_cached_contacts()
        if contacts:
            self._apply_contacts(contacts)
            return
        if self._contacts_busy:
            return
        self._contacts_busy = True
        self._contacts_list.addWidget(lbl("Loading contacts…", 12, TEXT_DIM))
        self._contacts_worker = ContactsWorker(self.adb.target, limit=500)
        self._contacts_worker.done.connect(self._on_contacts_loaded)
        self._contacts_worker.finished.connect(self._contacts_worker.deleteLater)
        self._contacts_worker.start()

    def _on_contacts_loaded(self, contacts):
        self._contacts_busy = False
        self._apply_contacts(contacts or [])

    def _apply_contacts(self, contacts):
        # Deduplicate by phone while preserving first-seen order
        seen = set()
        deduped = []
        for c in contacts:
            phone = (c.get("phone") or "").strip()
            if not phone or phone in seen:
                continue
            seen.add(phone)
            deduped.append({
                "name": (c.get("name") or phone).strip(),
                "phone": phone,
            })
        self._contacts = deduped
        self._render_contacts(self._contacts)

    def _toggle_contacts_section(self):
        expanded = self._contacts_body.isHidden()
        self._contacts_body.setHidden(not expanded)
        self._contacts_toggle.setText("▾" if expanded else "▸")
        if expanded and not self._contacts:
            self._load_contacts()

    def _toggle_history_section(self):
        expanded = self._history_body.isHidden()
        self._history_body.setHidden(not expanded)
        self._history_toggle.setText("▾" if expanded else "▸")
        if expanded and not self._call_history:
            self._load_history_from_phone()

    def _filter_contacts(self, query):
        if not query:
            self._render_contacts(self._contacts)
            return
        q = query.lower()
        filtered = [c for c in self._contacts
                    if q in c.get("name","").lower() or q in c.get("phone","")]
        self._render_contacts(filtered)

    def _render_contacts(self, contacts):
        while self._contacts_list.count():
            item = self._contacts_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not contacts:
            self._contacts_list.addWidget(
                lbl("No contacts loaded · tap Sync first", 12, TEXT_DIM))
            return

        for c in contacts[:30]:
            row = QWidget()
            row.setStyleSheet("""
                QWidget { background:transparent;border:none; }
                QWidget:hover { background:rgba(255,255,255,0.04); }
            """)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8,7,8,7)
            rl.setSpacing(12)

            avatar = QLabel(c.get("name","?")[0].upper() if c.get("name") else "?")
            avatar.setFixedSize(34,34)
            avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            avatar.setStyleSheet(f"""
                QLabel {{
                    background: rgba(167,139,250,0.1);
                    border: 1px solid rgba(167,139,250,0.2);
                    border-radius: 10px;
                    color: {TEAL};
                    font-size: 14px;
                    font-weight: 600;
                }}
            """)
            rl.addWidget(avatar)

            info = QVBoxLayout()
            info.setSpacing(1)
            info.addWidget(lbl(c.get("name","Unknown"), 13, bold=True))
            info.addWidget(lbl(c.get("phone",""), 11, TEXT_DIM, mono=True))
            rl.addLayout(info)
            rl.addStretch()

            call_btn = QPushButton("📞")
            call_btn.setFixedSize(30,30)
            call_btn.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(167,139,250,0.08);
                    border:1px solid rgba(167,139,250,0.2);
                    border-radius:8px;font-size:14px;
                }}
                QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
            """)
            phone = c.get("phone","")
            call_btn.clicked.connect(lambda _, p=phone: self._call_contact(p))
            rl.addWidget(call_btn)

            self._contacts_list.addWidget(row)

    def _call_contact(self, phone):
        if phone:
            self._dial_input.setText(phone)
            self._place_call()

    def _toggle_live_mute(self):
        if not self._call_started:
            return
        self._call_muted = not self._call_muted
        adb_ok = self.adb.set_call_muted(self._call_muted)
        local_ok = self._set_local_mic_mute(self._call_muted) if bool(state.get("call_audio_active", False)) else False
        ok = bool(adb_ok or local_ok)
        self._mute_btn.setText("Unmute" if self._call_muted else "Mute")
        if not ok and self._call_muted:
            # Avoid toggle keyevents; they can desync mute state on some OEM builds.
            self.adb.set_call_muted(False)
            self._set_local_mic_mute(False)
            self._call_muted = False
            self._mute_btn.setText("Mute")
        if ok:
            push_toast("Call muted" if self._call_muted else "Call unmuted", "success" if self._call_muted else "info", 1400)
        else:
            push_toast("Mute may be blocked by device policy", "warning", 1900)

    def _toggle_call_audio_route(self):
        if not self._call_started:
            push_toast("Call must be active before changing route", "info", 1500)
            return
        active = bool(state.get("call_audio_active", False))
        pending = str(state.get("call_route_status", "")) == "pending_pc"
        if active or pending or self._pc_route_requested:
            self._pc_route_requested = False
            self._pc_route_retry_after_connect_done = True
            audio_route.set_source("call_pc_active", False)
            audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
            self._release_bt_call_route()
            state.set("call_ui_state", {
                "status": "active",
                "number": str((state.get("call_ui_state", {}) or {}).get("number") or ""),
                "contact_name": str((state.get("call_ui_state", {}) or {}).get("contact_name") or ""),
                "audio_target": "phone",
                "updated_at": int(time.time() * 1000),
            })
            push_toast("Switched call audio to phone", "info", 1600)
            self._update_live_controls()
            return
        self._pc_route_requested = True
        self._pc_route_retry_after_connect_done = True
        call_ui = state.get("call_ui_state", {}) or {}
        number = str(call_ui.get("number") or self._dial_input.text().strip())
        name = str(call_ui.get("contact_name") or number)
        state.set("call_route_status", "pending_pc")
        state.set("call_route_reason", "Preparing laptop call audio...")
        state.set("call_route_backend", "none")
        self._start_call_route_attempt(number, name, intent="transfer")
        self._update_live_controls()

    def _end_call(self):
        if not self._call_started:
            return
        self.adb.end_call()
        audio_route.set_source("call_pc_active", False)
        audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
        state.set("outbound_call_origin", {})
        self._pc_route_requested = False
        self._pc_route_retry_after_connect_done = False
        self._call_started = False
        self._call_muted = False
        self.adb.set_call_muted(False)
        self._set_local_mic_mute(False)
        if hasattr(self, "_mute_btn"):
            self._mute_btn.setText("Mute")
        self._update_live_controls()

    def _start_call_route_attempt(self, number, contact_name, *, intent):
        if self._call_route_worker is not None:
            try:
                if self._call_route_worker.isRunning():
                    return
            except RuntimeError:
                self._call_route_worker = None
        self._call_route_context = {
            "number": str(number or ""),
            "contact_name": str(contact_name or number or ""),
            "intent": str(intent or "transfer"),
        }
        if hasattr(self, "_route_live_btn"):
            self._route_live_btn.setEnabled(False)
        worker = CallRouteWorker(preferred_name=str(contact_name or number or ""))
        self._call_route_worker = worker
        worker.done.connect(self._on_call_route_done)
        worker.finished.connect(lambda: self._on_call_route_worker_finished(worker))
        worker.start()

    def _on_call_route_done(self, result):
        ctx = dict(self._call_route_context or {})
        number = str(ctx.get("number") or "")
        contact_name = str(ctx.get("contact_name") or number)
        intent = str(ctx.get("intent") or "transfer")
        if result.ok and result.status == "active":
            state.set("call_ui_state", {
                "status": "active",
                "number": number,
                "contact_name": contact_name,
                "audio_target": "pc",
                "updated_at": int(time.time() * 1000),
            })
            if intent == "outbound_auto":
                push_toast(f"Calling {number} on laptop audio", "success", 1700)
            elif intent == "answer":
                push_toast("Call answered on laptop audio", "success", 1600)
            else:
                push_toast("Transferred call audio to laptop", "success", 1600)
            return
        self._pc_route_requested = False
        state.set("call_ui_state", {
            "status": "active",
            "number": number,
            "contact_name": contact_name,
            "audio_target": "phone",
            "updated_at": int(time.time() * 1000),
        })
        state.set("call_route_status", "pc_failed")
        state.set("call_route_reason", str(result.reason or "Laptop call audio unavailable"))
        state.set("call_route_backend", "none")
        if intent == "outbound_auto":
            push_toast(f"Calling {number} (laptop audio unavailable)", "warning", 2000)
        elif intent == "answer":
            push_toast("Call answered, but laptop audio route failed", "warning", 1900)
        else:
            push_toast("Could not switch call audio route", "warning", 1900)
        self._update_live_controls()

    def _on_call_route_worker_finished(self, worker):
        if hasattr(self, "_route_live_btn"):
            self._route_live_btn.setEnabled(True)
        if self._call_route_worker is worker:
            self._call_route_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass
        self._update_live_controls()

    def _update_live_controls(self):
        started = bool(self._call_started)
        route_active = bool(state.get("call_audio_active", False))
        route_pending = str(state.get("call_route_status", "")) == "pending_pc"
        phone_switch_mode = bool(self._pc_route_requested or route_active or route_pending)

        if hasattr(self, "_end_btn"):
            self._end_btn.setEnabled(started)
        if hasattr(self, "_mute_btn"):
            self._mute_btn.setEnabled(started)
        if hasattr(self, "_route_live_btn"):
            if phone_switch_mode:
                self._route_live_btn.setText("Switch to Phone Audio")
            else:
                self._route_live_btn.setText("Switch to Laptop Audio")
            self._route_live_btn.setEnabled(started and self._call_route_worker is None)

    @staticmethod
    def _release_bt_call_route() -> bool:
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            if not mgr.available():
                return False
            hints = [
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            changed, _ = mgr.release_call_audio_route(hints, force_disconnect=True)
            return bool(changed)
        except Exception:
            return False

    @staticmethod
    def _set_local_mic_mute(muted: bool) -> bool:
        try:
            from backend import call_audio

            if call_audio.set_input_muted(bool(muted)):
                return True
        except Exception:
            pass
        target = "1" if muted else "0"
        commands = [
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", target],
            ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", target],
        ]
        for cmd in commands:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    def add_call(self, event, number, contact_name):
        """Called by window when telephony signal fires"""
        self._call_history.insert(0, {
            "event": event,
            "number": number,
            "name": contact_name or number,
            "date_ms": int(time.time() * 1000),
        })
        self._refresh_history()

    def _refresh_history(self):
        while self._history_list.count():
            item = self._history_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._call_history:
            self._history_list.addWidget(
                lbl("No recent calls", 12, TEXT_DIM))
            return

        for call in self._call_history[:20]:
            event = call.get("event", "")
            if event in {"talking", "incoming"}:
                color, ico = TEAL, "📥"
            elif event in {"outgoing", "ringing"}:
                color, ico = CYAN, "📤"
            elif event in {"missed_call", "missed", "rejected"}:
                color, ico = ROSE, "📵"
            else:
                color, ico = TEXT_DIM, "📞"
            row = QWidget()
            row.setStyleSheet("background:transparent;border:none;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8,6,8,6)
            rl.setSpacing(10)
            rl.addWidget(lbl(ico, 18))
            rl.addWidget(lbl(call.get("name", call.get("number", "Unknown")), 13, bold=True))
            rl.addStretch()
            rl.addWidget(lbl(call.get("number", ""), 11, TEXT_DIM, mono=True))
            call_btn = QPushButton("Call")
            call_btn.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(167,139,250,0.08);
                    border:1px solid rgba(167,139,250,0.2);
                    border-radius:7px;color:{TEAL};padding:4px 8px;font-size:10px;
                }}
                QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
            """)
            num = call.get("number", "")
            call_btn.clicked.connect(lambda _, n=num: self._call_contact(n))
            rl.addWidget(call_btn)
            self._history_list.addWidget(row)

    def refresh(self):
        now = time.time()
        if now - self._last_history_refresh > 20:
            self._last_history_refresh = now
            self._load_history_from_phone()
        if not self._contacts_body.isHidden() and not self._contacts and not self._contacts_busy:
            self._load_contacts()

    def _load_history_from_phone(self):
        if self._history_busy:
            return
        self._history_busy = True
        self._history_worker = CallsHistoryWorker(self.adb.target, limit=40)
        self._history_worker.done.connect(self._on_history_loaded)
        self._history_worker.finished.connect(self._history_worker.deleteLater)
        self._history_worker.start()

    def _on_history_loaded(self, rows):
        self._history_busy = False
        if rows:
            self._call_history = rows
        self._refresh_history()
