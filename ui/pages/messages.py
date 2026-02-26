"""Messages page — notifications + SMS"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QLineEdit,
                              QTextEdit, QComboBox, QCompleter)
from PyQt6.QtCore import Qt, QTimer
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      text_area, divider, TEAL, CYAN, VIOLET, ROSE, AMBER,
                      TEXT, TEXT_DIM, TEXT_MID, FROST, BORDER)
from backend.kdeconnect import KDEConnect
from backend.adb_bridge import ADBBridge
from backend.state import state
import logging

log = logging.getLogger(__name__)

APP_COLORS = {
    "Messages":       TEAL,
    "Phone":          CYAN,
    "WhatsApp":       CYAN,
    "Gmail":          TEAL,
    "Instagram":      CYAN,
    "Telegram":       CYAN,
    "Authenticator":  AMBER,
    "Amazon":         AMBER,
}

def app_color(app_name):
    for k, v in APP_COLORS.items():
        if k.lower() in (app_name or "").lower():
            return v
    return TEAL

class MessagesPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc = KDEConnect()
        self.adb = ADBBridge()
        self._contacts = []
        self._build()
        self.refresh()
        QTimer.singleShot(500, self._load_contacts)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(14)

        layout.addWidget(lbl("Messages", 22, bold=True))
        guide = card_frame()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.setSpacing(4)
        gl.addWidget(section_label("Flow"))
        gl.addWidget(lbl("1) Review notifications  2) Dismiss or quick-reply  3) Send SMS from synced contacts", 11, TEXT_DIM))
        layout.addWidget(guide)

        # ── Notifications ────────────────────────────────────────
        notif_frame = card_frame()
        nl = QVBoxLayout(notif_frame)
        nl.setContentsMargins(20,16,20,16)
        nl.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.addWidget(section_label("Phone Notifications"))
        hdr.addStretch()
        clear_btn = QPushButton("Clear All")
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.08);
                border-radius:8px;color:{TEXT_DIM};padding:5px 10px;font-size:10px;
            }}
            QPushButton:hover {{ color:{ROSE};border-color:{ROSE}44; }}
        """)
        clear_btn.clicked.connect(self._clear_all)
        hdr.addWidget(clear_btn)
        refresh_btn = QPushButton("↻")
        refresh_btn.setFixedSize(28,28)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;
                color:{TEXT_DIM};font-size:16px;border-radius:6px;
            }}
            QPushButton:hover {{ color:{TEAL}; }}
        """)
        refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(refresh_btn)
        nl.addLayout(hdr)

        self._notif_container = QVBoxLayout()
        self._notif_container.setSpacing(6)
        nl.addLayout(self._notif_container)
        layout.addWidget(notif_frame)

        # ── SMS Compose ──────────────────────────────────────────
        sms_frame = card_frame()
        sl = QVBoxLayout(sms_frame)
        sl.setContentsMargins(20,16,20,18)
        sl.setSpacing(10)
        sl.addWidget(section_label("Send SMS"))

        self._sms_contact = QComboBox()
        self._sms_contact.setEditable(True)
        self._sms_contact.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._sms_contact.setStyleSheet(f"""
            QComboBox {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.09);
                border-radius:10px;color:white;padding:8px 10px;font-size:12px;
            }}
            QComboBox:hover {{ border-color:rgba(62,240,176,0.25); }}
        """)
        completer = QCompleter(self._sms_contact.model(), self._sms_contact)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._sms_contact.setCompleter(completer)
        self._sms_contact.currentIndexChanged.connect(self._pick_contact)
        self._sms_number = input_field("+1 phone number")
        self._sms_text   = text_area("Message…", 80)
        self._sms_status = lbl("", 10, TEXT_DIM)

        send_btn = action_btn("Send SMS →", TEAL)
        send_btn.clicked.connect(self._send_sms)

        sl.addWidget(self._sms_contact)
        sl.addWidget(self._sms_number)
        sl.addWidget(self._sms_text)
        sl.addWidget(self._sms_status)
        sl.addWidget(send_btn)
        layout.addWidget(sms_frame)
        layout.addStretch()

    def refresh(self):
        # Clear
        while self._notif_container.count():
            item = self._notif_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._consume_sms_draft()

        notifs = self.kc.get_notifications()
        from backend.state import state
        state.set("notifications", notifs)
        if not notifs:
            empty = lbl("No notifications · check KDE Connect is connected",
                        12, TEXT_DIM)
            self._notif_container.addWidget(empty)
            return

        for n in notifs[:12]:
            self._notif_container.addWidget(self._notif_row(n))

    def _notif_row(self, n):
        color = app_color(n.get("app",""))
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.025);
                border: 1px solid transparent;
                border-left: 2px solid {color};
                border-radius: 10px;
            }}
            QFrame:hover {{
                background: rgba(255,255,255,0.05);
                border-color: rgba(255,255,255,0.07);
                border-left-color: {color};
            }}
        """)
        rl = QVBoxLayout(row)
        rl.setContentsMargins(14,10,14,10)
        rl.setSpacing(4)

        top = QHBoxLayout()
        app_lbl = lbl(n.get("app",""), 9, TEXT_DIM, mono=True)
        time_lbl = lbl("just now", 9, TEXT_DIM, mono=True)
        top.addWidget(app_lbl)
        top.addStretch()
        top.addWidget(time_lbl)
        rl.addLayout(top)

        rl.addWidget(lbl(n.get("title",""), 13, TEXT, bold=True))
        if n.get("text"):
            rl.addWidget(lbl(n.get("text",""), 11, TEXT_MID, wrap=True))

        # Actions row
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)

        if n.get("replyId"):
            reply_input = QLineEdit()
            reply_input.setPlaceholderText("Quick reply…")
            reply_input.setStyleSheet(f"""
                QLineEdit {{
                    background:rgba(255,255,255,0.05);
                    border:1px solid rgba(255,255,255,0.07);
                    border-radius:7px;color:white;
                    padding:5px 10px;font-size:11px;
                }}
                QLineEdit:focus {{ border-color:rgba(62,240,176,0.35); }}
            """)
            send = QPushButton("↑")
            send.setFixedSize(28,28)
            send.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(62,240,176,0.1);
                    border:1px solid rgba(62,240,176,0.25);
                    border-radius:7px;color:{TEAL};font-size:14px;
                }}
                QPushButton:hover {{ background:rgba(62,240,176,0.2); }}
            """)
            reply_id = n["replyId"]
            send.clicked.connect(lambda _, ri=reply_id, inp=reply_input:
                                  self.kc.reply_notification(ri, inp.text()))
            actions_row.addWidget(reply_input)
            actions_row.addWidget(send)

        dismiss = QPushButton("✕")
        dismiss.setFixedSize(24,24)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background:transparent;border:none;
                color:{TEXT_DIM};font-size:12px;border-radius:4px;
            }}
            QPushButton:hover {{ color:{ROSE}; }}
        """)
        nid = n["id"]
        dismiss.clicked.connect(lambda _, i=nid: self._dismiss(i, row))
        actions_row.addWidget(dismiss)

        if actions_row.count():
            rl.addLayout(actions_row)

        return row

    def _dismiss(self, notif_id, row_widget):
        self.kc.dismiss_notification(notif_id)
        row_widget.hide()
        row_widget.deleteLater()

    def _clear_all(self):
        for n in self.kc.get_notifications():
            self.kc.dismiss_notification(n.get("id", ""))
        QTimer.singleShot(250, self.refresh)

    def _send_sms(self):
        number = self._sms_number.text().strip()
        text   = self._sms_text.toPlainText().strip()
        if not number or not text:
            self._sms_status.setText("Enter a phone number and message.")
            return
        ok = self.kc.send_sms(number, text)
        if ok:
            self._sms_text.clear()
            self._sms_status.setText("Message sent.")
        else:
            self._sms_status.setText("Failed to send. Check KDE Connect SMS permissions.")
            log.warning("SMS send failed for %s", number)

    def _load_contacts(self):
        self._contacts = self.kc.get_cached_contacts()
        recent = self.adb.get_recent_calls(limit=80)
        items = []
        seen = set()

        for row in recent:
            phone = (row.get("number") or "").strip()
            if not phone or phone in seen:
                continue
            seen.add(phone)
            name = (row.get("name") or phone).strip()
            items.append((f"Recent · {name} · {phone}", phone))

        for c in self._contacts[:300]:
            name = c.get("name", "").strip() or c.get("phone", "Unknown")
            phone = c.get("phone", "").strip()
            if not phone or phone in seen:
                continue
            seen.add(phone)
            items.append((f"Contact · {name} · {phone}", phone))

        self._sms_contact.clear()
        self._sms_contact.addItem("Choose contact…", "")
        for text, phone in items:
            self._sms_contact.addItem(text, phone)

    def _pick_contact(self):
        phone = self._sms_contact.currentData() or ""
        if phone:
            self._sms_number.setText(str(phone))

    def _consume_sms_draft(self):
        draft = (state.get("sms_draft_number", "") or "").strip()
        if not draft:
            return
        self._sms_number.setText(draft)
        self._sms_text.setFocus()
        state.set("sms_draft_number", "")
