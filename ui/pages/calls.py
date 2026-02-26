"""Calls page — place calls, contacts, call history"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame,
                              QGridLayout)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      divider, TEAL, CYAN, VIOLET, ROSE, AMBER, TEXT,
                      TEXT_DIM, TEXT_MID, BORDER)
from backend.kdeconnect import KDEConnect
from backend.adb_bridge import ADBBridge
from backend.ui_feedback import push_toast
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
        self._build()
        self.refresh()

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
            ("1",""),("2","ABC"),("3","DEF"),
            ("4","GHI"),("5","JKL"),("6","MNO"),
            ("7","PQRS"),("8","TUV"),("9","WXYZ"),
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
                    background: rgba(62,240,176,0.08);
                    border-color: rgba(62,240,176,0.2);
                }}
                QPushButton:pressed {{
                    background: rgba(62,240,176,0.15);
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
        ans_btn = action_btn("Answer", TEAL)
        ans_btn.clicked.connect(lambda: self.adb.answer_call())
        rej_btn = action_btn("Reject", ROSE)
        rej_btn.clicked.connect(lambda: self.adb.end_call())
        end_btn = action_btn("End", ROSE)
        end_btn.clicked.connect(lambda: self.adb.end_call())
        self._mute_btn = action_btn("Mute", AMBER)
        self._mute_btn.clicked.connect(self._toggle_live_mute)
        route_live_btn = action_btn("Use Laptop Audio", CYAN)
        route_live_btn.clicked.connect(lambda: self.adb.launch_scrcpy("audio"))
        for btn in (ans_btn, rej_btn, end_btn, self._mute_btn, route_live_btn):
            live_row.addWidget(btn)
        dl.addLayout(live_row)
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
        self.adb._run("shell", "am", "start",
                       "-a", "android.intent.action.CALL",
                       "-d", f"tel:{number}")
        push_toast(f"Calling {number}", "info", 1600)

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
                    background: rgba(62,240,176,0.1);
                    border: 1px solid rgba(62,240,176,0.2);
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
                    background:rgba(62,240,176,0.08);
                    border:1px solid rgba(62,240,176,0.2);
                    border-radius:8px;font-size:14px;
                }}
                QPushButton:hover {{ background:rgba(62,240,176,0.18); }}
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
        self._call_muted = not self._call_muted
        ok = self.adb.set_call_muted(self._call_muted)
        self._mute_btn.setText("Unmute" if self._call_muted else "Mute")
        if not ok and self._call_muted:
            # Fallback: hard-lower call output volume if device mute is restricted.
            for _ in range(6):
                self.adb._run("shell", "input", "keyevent", "KEYCODE_VOLUME_DOWN", timeout=1)
        if ok:
            push_toast("Call muted" if self._call_muted else "Call unmuted", "success" if self._call_muted else "info", 1400)
        else:
            push_toast("Mute command sent (restricted by device)", "warning", 1900)

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
                    background:rgba(62,240,176,0.08);
                    border:1px solid rgba(62,240,176,0.2);
                    border-radius:7px;color:{TEAL};padding:4px 8px;font-size:10px;
                }}
                QPushButton:hover {{ background:rgba(62,240,176,0.18); }}
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
