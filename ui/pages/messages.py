"""Messages page — notifications + SMS"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QLineEdit,
                              QTextEdit, QComboBox, QCompleter, QApplication, QGraphicsOpacityEffect)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup, pyqtSignal, QPoint, pyqtProperty, QSize, QThread, QObject
import time
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      text_area, divider, with_alpha, TEAL, CYAN, VIOLET, ROSE, AMBER,
                      TEXT, TEXT_DIM, TEXT_MID, FROST, BORDER)
from backend.kdeconnect import KDEConnect
from backend.adb_bridge import ADBBridge
from backend.state import state
from backend.notification_mirror import (
    sync_desktop_notifications,
    close_phone_notification,
    clear_phone_notifications,
)
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


def _fmt_age(time_ms: int) -> str:
    """Return a human-readable age string for a notification timestamp (ms epoch)."""
    if not time_ms:
        return "just now"
    age = time.time() - time_ms / 1000.0
    if age < 5:
        return "just now"
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


class _NotifFetchWorker(QObject):
    """Background worker: fetches notifications off the Qt main thread."""
    finished = pyqtSignal(list)

    def __init__(self, kc: KDEConnect):
        super().__init__()
        self._kc = kc

    def run(self):
        try:
            notifs = self._kc.get_notifications()
        except Exception:
            notifs = []
        # Preserve existing time_ms from state for notifications we already know
        existing: dict[str, dict] = {
            str((r or {}).get("id") or ""): r
            for r in (state.get("notifications") or [])
            if (r or {}).get("id")
        }
        now_ms = int(time.time() * 1000)
        for n in notifs:
            nid = str(n.get("id") or "")
            if not n.get("time_ms") and nid in existing and existing[nid].get("time_ms"):
                n["time_ms"] = existing[nid]["time_ms"]
            elif not n.get("time_ms"):
                n["time_ms"] = now_ms
        # Newest-first: higher time_ms first; stable secondary sort on id string
        notifs.sort(key=lambda x: (-int(x.get("time_ms") or 0), str(x.get("id") or "")))
        self.finished.emit(notifs)

class SwipeNotifRow(QFrame):
    dismissed = pyqtSignal(str, object)

    def __init__(self, notif_id, color):
        super().__init__()
        self.notif_id = str(notif_id or "")
        self._accent = str(color or "#f05252")
        self._press_global_x = None
        self._dragging = False
        self._offset_x = 0
        self._bg_reveal = 0.0
        self._dismissed = False
        self.setMinimumHeight(0)
        self.setStyleSheet("background:transparent;border:none;")

        self._bg = QFrame(self)
        self._bg.setStyleSheet("background: rgba(240,82,82,0.0); border-radius: 10px; border:none;")

        self._bg_icon = QLabel("✕", self._bg)
        self._bg_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bg_icon.setStyleSheet("color: rgba(255,255,255,0.0); font-size: 12px; font-weight: 700; background: transparent; border:none;")

        self._content = QFrame(self)
        self._content.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.025);
                border: 1px solid transparent;
                border-left: 2px solid {self._accent};
                border-radius: 10px;
            }}
            QFrame:hover {{
                background: rgba(255,255,255,0.05);
                border-color: rgba(255,255,255,0.07);
                border-left-color: {self._accent};
            }}
        """)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 10, 14, 10)
        self._content_layout.setSpacing(4)

    def content_layout(self):
        return self._content_layout

    def getBgReveal(self):
        return self._bg_reveal

    def setBgReveal(self, value):
        self._bg_reveal = max(0.0, min(1.0, float(value)))
        alpha = int(180 * self._bg_reveal)
        icon_alpha = int(255 * self._bg_reveal)
        self._bg.setStyleSheet(f"background: rgba(240,82,82,{alpha}); border-radius: 10px; border:none;")
        self._bg_icon.setStyleSheet(
            f"color: rgba(255,255,255,{icon_alpha}); font-size: 12px; font-weight: 700; background: transparent; border:none;"
        )

    bgReveal = pyqtProperty(float, fget=getBgReveal, fset=setBgReveal)

    def sizeHint(self):
        h = max(68, self._content_layout.sizeHint().height() + 2)
        return QSize(220, h)

    def minimumSizeHint(self):
        return self.sizeHint()

    def reflow(self):
        h = self.sizeHint().height()
        self.setMinimumHeight(h)
        if self.maximumHeight() <= 0 or self.maximumHeight() > 10000:
            self.setMaximumHeight(h)
        self.updateGeometry()

    def resizeEvent(self, event):
        self._bg.setGeometry(self.rect())
        self._bg_icon.setGeometry(self.width() - 32, 0, 22, self.height())
        self._content.setGeometry(self._offset_x, 0, self.width(), self.height())
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global_x = event.globalPosition().x()
            self._dragging = True
            self._offset_x = 0
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._press_global_x is not None:
            delta = int(event.globalPosition().x() - self._press_global_x)
            if delta > 0:
                delta = 0
            self._offset_x = max(-80, delta)
            self._content.move(self._offset_x, 0)
            self.setBgReveal(min(1.0, abs(self._offset_x) / 80.0))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            if abs(self._offset_x) >= 50:
                self.animate_dismiss(direction=-1)
            else:
                self._snap_back()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _snap_back(self):
        move = QPropertyAnimation(self._content, b"pos", self)
        move.setDuration(200)
        move.setEasingCurve(QEasingCurve.Type.OutBack)
        move.setStartValue(self._content.pos())
        move.setEndValue(QPoint(0, 0))

        reveal = QPropertyAnimation(self, b"bgReveal", self)
        reveal.setDuration(150)
        reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        reveal.setStartValue(self._bg_reveal)
        reveal.setEndValue(0.0)

        group = QParallelAnimationGroup(self)
        group.addAnimation(move)
        group.addAnimation(reveal)
        group.finished.connect(self._on_snap_back_finished)
        group.start()
        self._dismiss_anim = group

    def _on_snap_back_finished(self):
        self._offset_x = 0
        self._content.move(0, 0)
        self.setBgReveal(0.0)

    def animate_dismiss(self, direction=-1, duration=180):
        if self._dismissed:
            return
        self._dismissed = True
        slide = QPropertyAnimation(self._content, b"pos", self)
        slide.setDuration(duration)
        slide.setEasingCurve(QEasingCurve.Type.InCubic)
        slide.setStartValue(self._content.pos())
        slide.setEndValue(QPoint(-self.width(), 0))

        reveal = QPropertyAnimation(self, b"bgReveal", self)
        reveal.setDuration(duration)
        reveal.setStartValue(max(self._bg_reveal, 0.35))
        reveal.setEndValue(1.0)

        group = QParallelAnimationGroup(self)
        for a in (slide, reveal):
            group.addAnimation(a)
        group.finished.connect(self._collapse_after_slide)
        group.start()
        self._dismiss_anim = group

    def _collapse_after_slide(self):
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
            effect.setOpacity(1.0)

        collapse_h = QPropertyAnimation(self, b"maximumHeight", self)
        collapse_h.setDuration(220)
        collapse_h.setEasingCurve(QEasingCurve.Type.OutCubic)
        collapse_h.setStartValue(max(1, self.height()))
        collapse_h.setEndValue(0)

        collapse_min = QPropertyAnimation(self, b"minimumHeight", self)
        collapse_min.setDuration(220)
        collapse_min.setEasingCurve(QEasingCurve.Type.OutCubic)
        collapse_min.setStartValue(max(1, self.height()))
        collapse_min.setEndValue(0)

        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(220)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(effect.opacity())
        fade.setEndValue(0.0)

        group = QParallelAnimationGroup(self)
        for anim in (collapse_h, collapse_min, fade):
            group.addAnimation(anim)
        group.finished.connect(lambda: self.dismissed.emit(self.notif_id, self))
        group.start()
        self._dismiss_anim = group

class MessagesPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc = KDEConnect()
        self.adb = ADBBridge()
        self._contacts = []
        self._notif_rows = []
        self._clear_all_in_progress = False
        self._fetch_thread: QThread | None = None
        self._build()
        state.subscribe("notif_revision", self._on_notif_revision)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._refresh_if_visible)
        self._poll_timer.start(2500)
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
            QPushButton:hover {{ color:{ROSE};border-color:{with_alpha(ROSE, 0.28)}; }}
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
            QComboBox:hover {{ border-color:rgba(167,139,250,0.25); }}
        """)
        popup_style = f"""
            QAbstractItemView {{
                background:#13161d;
                border:1px solid #252b3b;
                border-radius:10px;
                color:{TEXT};
                selection-background-color:{with_alpha(VIOLET, 0.28)};
                selection-color:{TEXT};
                padding:4px;
            }}
            QAbstractItemView::item {{
                min-height:24px;
                padding:4px 8px;
            }}
            QAbstractItemView::item:hover {{
                background:{with_alpha(VIOLET, 0.18)};
            }}
        """
        self._sms_contact.view().setStyleSheet(popup_style)
        completer = QCompleter(self._sms_contact.model(), self._sms_contact)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._sms_contact.setCompleter(completer)
        if completer.popup() is not None:
            completer.popup().setStyleSheet(popup_style)
        if self._sms_contact.lineEdit():
            self._sms_contact.lineEdit().setPlaceholderText("Type name or number…")
            self._sms_contact.lineEdit().clear()
        self._sms_contact.setCurrentIndex(-1)
        self._sms_contact.currentIndexChanged.connect(self._pick_contact)
        self._sms_number = input_field("+1 phone number")
        self._sms_number_completer = QCompleter([], self._sms_number)
        self._sms_number_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._sms_number_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._sms_number.setCompleter(self._sms_number_completer)
        if self._sms_number_completer.popup() is not None:
            self._sms_number_completer.popup().setStyleSheet(popup_style)
        self._sms_text   = text_area("Message…", 80)
        self._sms_status = lbl("", 10, TEXT_DIM)

        send_btn = action_btn("Send SMS →", TEAL)
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(167,139,250,0.20);
                border: 1px solid rgba(167,139,250,0.46);
                border-radius: 10px;
                color: {TEAL};
                padding: 9px 14px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(167,139,250,0.28);
                border-color: rgba(167,139,250,0.62);
            }}
            QPushButton:pressed {{
                background: rgba(167,139,250,0.36);
                border-color: rgba(167,139,250,0.75);
            }}
        """)
        send_btn.clicked.connect(self._send_sms)

        sl.addWidget(self._sms_contact)
        sl.addWidget(self._sms_number)
        sl.addWidget(self._sms_text)
        sl.addWidget(self._sms_status)
        sl.addWidget(send_btn)
        layout.addWidget(sms_frame)
        layout.addStretch()

    def refresh(self):
        if self._clear_all_in_progress:
            return
        # If a fetch is already in flight, skip — result will arrive via _on_notifs_fetched.
        if self._fetch_thread is not None and self._fetch_thread.isRunning():
            return
        self._consume_sms_draft()  # must run on Qt thread — safe here
        thread = QThread(self)
        worker = _NotifFetchWorker(self.kc)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_notifs_fetched)
        worker.finished.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._fetch_thread = thread
        thread.start()

    def _on_notifs_fetched(self, notifs: list):
        if self._clear_all_in_progress:
            return
        # Clear existing rows
        self._notif_rows = []
        while self._notif_container.count():
            item = self._notif_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        state.set("notifications", notifs)
        sync_desktop_notifications(notifs)

        if not notifs:
            empty = lbl("No notifications · check KDE Connect is connected",
                        12, TEXT_DIM)
            self._notif_container.addWidget(empty)
            return

        for n in notifs[:12]:
            self._notif_container.addWidget(self._notif_row(n))

    def _refresh_if_visible(self):
        if self.isVisible() and not self._clear_all_in_progress:
            self.refresh()

    def _on_notif_revision(self, _payload):
        if self.isVisible() and not self._clear_all_in_progress:
            QTimer.singleShot(80, self.refresh)

    def _notif_row(self, n):
        color = app_color(n.get("app",""))
        row = SwipeNotifRow(n.get("id", ""), color)
        row.dismissed.connect(self._on_swipe_dismissed)
        rl = row.content_layout()

        top = QHBoxLayout()
        app_lbl = lbl(n.get("app",""), 9, TEXT_DIM, mono=True)
        time_lbl = lbl(_fmt_age(n.get("time_ms", 0)), 9, TEXT_DIM, mono=True)
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
                QLineEdit:focus {{ border-color:rgba(167,139,250,0.35); }}
            """)
            send = QPushButton("↑")
            send.setFixedSize(28,28)
            send.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(167,139,250,0.1);
                    border:1px solid rgba(167,139,250,0.25);
                    border-radius:7px;color:{TEAL};font-size:14px;
                }}
                QPushButton:hover {{ background:rgba(167,139,250,0.2); }}
            """)
            reply_id = n["replyId"]
            send.clicked.connect(lambda _, ri=reply_id, inp=reply_input:
                                  self.kc.reply_notification(ri, inp.text()))
            actions_row.addWidget(reply_input)
            actions_row.addWidget(send)

        if actions_row.count():
            rl.addLayout(actions_row)

        row.reflow()
        self._notif_rows.append(row)
        return row

    def _dismiss(self, notif_id, row_widget):
        self.kc.dismiss_notification(notif_id)
        close_phone_notification(str(notif_id))
        row_widget.hide()
        row_widget.deleteLater()
        state.set("notif_revision", {"id": str(notif_id), "updated_at": int(time.time() * 1000)})
        QTimer.singleShot(140, self.refresh)

    def _on_swipe_dismissed(self, notif_id, row_widget):
        self.kc.dismiss_notification(notif_id)
        close_phone_notification(str(notif_id))
        row_widget.hide()
        row_widget.deleteLater()
        self._notif_rows = [r for r in self._notif_rows if r is not row_widget]
        if self._clear_all_in_progress:
            return
        state.set("notif_revision", {"id": str(notif_id), "updated_at": int(time.time() * 1000)})
        if not any(r.isVisible() for r in self._notif_rows):
            empty = lbl("No notifications", 12, TEXT_DIM)
            self._notif_container.addWidget(empty)
            effect = QGraphicsOpacityEffect(empty)
            empty.setGraphicsEffect(effect)
            effect.setOpacity(0.0)
            anim = QPropertyAnimation(effect, b"opacity", empty)
            anim.setDuration(200)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.start()
            empty._fade_anim = anim

    def _clear_all(self):
        rows = []
        for r in list(self._notif_rows):
            if r is None:
                continue
            try:
                if r.isVisible():
                    rows.append(r)
            except RuntimeError:
                continue
        if rows:
            self._clear_all_in_progress = True
            for idx, row in enumerate(rows):
                QTimer.singleShot(idx * 55, lambda rw=row: self._safe_animate_dismiss(rw))
            total = (max(0, len(rows) - 1) * 55) + 430
            QTimer.singleShot(total, self._finalize_clear_all)
            return
        self._finalize_clear_all()

    @staticmethod
    def _safe_animate_dismiss(row):
        try:
            if row is not None and row.isVisible():
                row.animate_dismiss(direction=-1, duration=200)
        except RuntimeError:
            pass

    def _finalize_clear_all(self):
        self._clear_all_in_progress = False
        rows = self.kc.get_notifications()
        for n in rows:
            self.kc.dismiss_notification(n.get("id", ""))
        clear_phone_notifications()
        state.set("notif_revision", {"id": "all_removed", "updated_at": int(time.time() * 1000)})
        QTimer.singleShot(220, self.refresh)

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
        self._sms_contact.setCurrentIndex(-1)
        if self._sms_contact.lineEdit():
            self._sms_contact.lineEdit().clear()
        phones = sorted({p for _, p in items if p})
        self._sms_number_completer.model().setStringList(phones)

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
