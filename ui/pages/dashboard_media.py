"""Media and clipboard helpers for the Dashboard page."""

from __future__ import annotations

import datetime
import os

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QTextEdit,
    QVBoxLayout,
)

import backend.settings_store as settings
from backend.clipboard_history import sanitize_clipboard_history
from backend.state import state
from ui.theme import CYAN, ROSE, TEAL, TEXT_DIM, VIOLET, action_btn, input_field, lbl, with_alpha


class DashboardMediaMixin:
    def _media_icon_btn(self, icon_name, color):
        _ = color
        button = QPushButton("")
        button.setFixedSize(42, 42)
        button.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 12px;
                padding: 0;
            }}
            QPushButton:hover {{
                background: {with_alpha(VIOLET, 0.10)};
                border-color: {with_alpha(VIOLET, 0.42)};
            }}
            QPushButton:pressed {{
                background: {with_alpha(VIOLET, 0.16)};
                border-color: {with_alpha(VIOLET, 0.56)};
            }}
        """)
        button.setIconSize(QSize(18, 18))
        self._set_media_button_icon(button, icon_name)
        button.pressed.connect(lambda btn=button: self._animate_media_btn(btn, down=True))
        button.released.connect(lambda btn=button: self._animate_media_btn(btn, down=False))
        return button

    def _set_media_button_icon(self, button, icon_name: str):
        style = QApplication.style()
        mapping = {
            "prev": QStyle.StandardPixmap.SP_MediaSeekBackward,
            "play": QStyle.StandardPixmap.SP_MediaPlay,
            "pause": QStyle.StandardPixmap.SP_MediaPause,
            "next": QStyle.StandardPixmap.SP_MediaSeekForward,
            "stop": QStyle.StandardPixmap.SP_MediaStop,
        }
        sp = mapping.get(icon_name, QStyle.StandardPixmap.SP_MediaPlay)
        base_icon = style.standardIcon(sp)
        size = button.iconSize()
        src = base_icon.pixmap(size)
        if src.isNull():
            button.setIcon(base_icon)
            return
        tinted = QPixmap(src.size())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, src)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(VIOLET))
        painter.end()
        button.setIcon(QIcon(tinted))

    def _animate_media_btn(self, btn, down):
        anim = QPropertyAnimation(btn, b"pos", btn)
        anim.setDuration(90)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        pos = btn.pos()
        target = pos + QPoint(0, 1) if down else pos - QPoint(0, 1)
        anim.setStartValue(pos)
        anim.setEndValue(target)
        anim.start()
        btn._press_anim = anim

    def _set_play_toggle_icon(self, media_state: str):
        state_name = (media_state or "").strip().lower()
        self._play_is_playing = state_name in {"playing", "active"}
        if hasattr(self, "_play_btn") and self._play_btn is not None:
            self._set_media_button_icon(self._play_btn, "pause" if self._play_is_playing else "play")

    @staticmethod
    def _is_valid_media_session(media: dict) -> bool:
        if not isinstance(media, dict):
            return False
        title = str(media.get("title") or "").strip()
        artist = str(media.get("artist") or "").strip()
        album = str(media.get("album") or "").strip()
        if not (title or artist or album):
            return False
        if title.lower() in {"media", "bluetooth", "unknown"} and not (artist or album):
            return False
        return True

    def _pick_display_media(self, current: dict, sessions: list):
        valid = [s for s in (sessions or []) if self._is_valid_media_session(s)]
        if not valid:
            return None, []
        preferred = str(self._active_media_pkg_pref or "").strip()
        if preferred:
            for session in valid:
                if str(session.get("package") or "").strip() == preferred:
                    return session, valid
        if self._is_valid_media_session(current):
            return current, valid
        active = [s for s in valid if str(s.get("state") or "").strip().lower() in {"playing", "active"}]
        return (active[0] if active else valid[0]), valid

    @staticmethod
    def _rounded_pixmap(pixmap: QPixmap, edge: int = 56, radius: int = 18) -> QPixmap:
        if pixmap.isNull():
            return QPixmap()
        scaled = pixmap.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        out = QPixmap(edge, edge)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        path = QPainterPath()
        path.addRoundedRect(0, 0, edge, edge, radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        return out

    @staticmethod
    def _placeholder_media_art(media: dict | None, edge: int = 56, radius: int = 18) -> QPixmap:
        out = QPixmap(edge, edge)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        path = QPainterPath()
        path.addRoundedRect(0, 0, edge, edge, radius, radius)
        painter.setClipPath(path)
        painter.fillRect(0, 0, edge, edge, QColor(30, 42, 68))
        painter.fillRect(0, int(edge * 0.55), edge, int(edge * 0.45), QColor(86, 112, 176))
        painter.setPen(QColor(220, 228, 245))
        marker = "▶"
        if isinstance(media, dict):
            title = str(media.get("title") or "").strip()
            if title:
                marker = title[0].upper()
        painter.drawText(out.rect(), Qt.AlignmentFlag.AlignCenter, marker)
        painter.end()
        return out

    def _set_now_playing_artwork(self, media: dict | None):
        art_path = ""
        if isinstance(media, dict):
            for key in ("artwork", "art", "album_art", "art_path", "cover_path", "display_icon_uri", "media_uri"):
                val = str(media.get(key) or "").strip()
                if val and os.path.exists(val):
                    art_path = val
                    break
        if art_path:
            pix = QPixmap(art_path)
            rounded = self._rounded_pixmap(pix)
            if not rounded.isNull():
                self._np_art.setText("")
                self._np_art.setPixmap(rounded)
                self._np_art.setStyleSheet("""
                    QLabel {
                        background: transparent;
                        border: none;
                    }
                """)
                return
        if isinstance(media, dict) and (media.get("title") or media.get("package") or media.get("session_name")):
            self._np_art.setText("")
            self._np_art.setPixmap(self._placeholder_media_art(media))
            self._np_art.setStyleSheet("""
                QLabel {
                    background: transparent;
                    border: none;
                }
            """)
            return
        self._np_art.setPixmap(QPixmap())
        self._np_art.setText("♪")
        self._np_art.setStyleSheet(f"""
            QLabel {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 18px;
                color: {TEXT_DIM};
                font-size: 20px;
                font-weight: 700;
            }}
        """)

    def _view_clipboard(self):
        history = sanitize_clipboard_history(state.get("clipboard_history", []) or [])
        current = (state.get("clipboard_text", "") or "").strip() or (QApplication.clipboard().text() or "").strip()
        dialog = QDialog(self)
        dialog.setWindowTitle("Synced Clipboard Timeline")
        dialog.setStyleSheet("background:#070c17;color:white;")
        dialog.resize(760, 380)
        lay = QVBoxLayout(dialog)
        lay.addWidget(lbl("Synced Clipboard Timeline", 13, bold=True))
        lay.addWidget(lbl("Shows clipboard items synced while PhoneBridge is active/background.", 10, TEXT_DIM))

        row = QHBoxLayout()
        row.setSpacing(10)

        history_list = QListWidget()
        history_list.setStyleSheet("""
            QListWidget {
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.1);
                border-radius:10px;
                padding:6px;
            }
            QListWidget::item {
                padding:6px 8px;
                border-radius:6px;
            }
            QListWidget::item:selected {
                background:rgba(167,139,250,0.16);
            }
        """)
        history_list.setMinimumWidth(300)
        search = input_field("Filter timeline…")
        lay.addWidget(search)

        te = QTextEdit()
        te.setPlainText(current or "(empty)")
        te.setStyleSheet("background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);border-radius:10px;color:white;padding:8px;")
        te.setReadOnly(False)

        def fill_rows(filter_text=""):
            history_list.clear()
            q = (filter_text or "").strip().lower()
            for entry in reversed(history):
                text = (entry.get("text") or "").strip()
                if not text:
                    continue
                src = (entry.get("source") or "phone").upper()
                if q and q not in text.lower() and q not in src.lower():
                    continue
                raw_ts = entry.get("ts")
                try:
                    ts = int(raw_ts or 0)
                except Exception:
                    ts = 0
                if ts > 10_000_000_000:
                    ts //= 1000
                try:
                    stamp = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "--:--:--"
                except Exception:
                    stamp = "--:--:--"
                preview = text.replace("\n", " ")[:52]
                item = QListWidgetItem(f"[{src}] {stamp}  {preview}")
                item.setData(Qt.ItemDataRole.UserRole, text)
                history_list.addItem(item)

        fill_rows()
        search.textChanged.connect(fill_rows)

        def on_pick(item):
            if not item:
                return
            te.setPlainText(item.data(Qt.ItemDataRole.UserRole) or "")

        history_list.currentItemChanged.connect(lambda curr, _: on_pick(curr))
        if history_list.count():
            history_list.setCurrentRow(0)

        row.addWidget(history_list)
        row.addWidget(te, 1)
        lay.addLayout(row)

        btn_row = QHBoxLayout()
        sync_btn = action_btn("Push to Phone", TEAL)

        def push_selected():
            QApplication.clipboard().setText(te.toPlainText())
            self.kc.send_clipboard_to_phone()

        sync_btn.clicked.connect(push_selected)
        copy_btn = action_btn("Copy to PC", CYAN)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(te.toPlainText()))
        clear_btn = action_btn("Clear History", ROSE)

        def clear_history():
            settings.set("clipboard_history", [])
            state.set("clipboard_history", [])
            history_list.clear()

        clear_btn.clicked.connect(clear_history)
        btn_row.addWidget(sync_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(clear_btn)
        lay.addLayout(btn_row)
        dialog.exec()

    def _media_cmd(self, action):
        target_pkg = str(self._active_media_pkg_pref or self._now_playing_pkg or "")
        if target_pkg and target_pkg != self._now_playing_pkg and action in {"prev", "toggle", "next"}:
            self.adb.launch_app(target_pkg)
        if action == "prev":
            self.adb.media_prev()
        elif action == "toggle":
            self.adb.media_play_pause()
            self._play_is_playing = not self._play_is_playing
            if hasattr(self, "_play_btn") and self._play_btn is not None:
                self._set_media_button_icon(self._play_btn, "pause" if self._play_is_playing else "play")
        elif action == "next":
            self.adb.media_next()
        elif action == "kill":
            self.adb.stop_media_app(target_pkg)
        QTimer.singleShot(250, lambda: self.refresh(force_media=True))

    def _sync_player_switch_button(self):
        count = len(self._media_sessions or [])
        self._player_switch_btn.setEnabled(count > 1)
        self._player_switch_btn.setText("➜" if count > 1 else "•")

    def _show_player_switch_menu(self):
        sessions = list(self._media_sessions or [])
        if len(sessions) <= 1:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#13161d; border:1px solid #252b3b; color:#dde3f0; }"
            "QMenu::item { padding:7px 12px; }"
            "QMenu::item:selected { background:rgba(124,108,255,0.22); }"
        )
        current_pkg = self._active_media_pkg_pref or self._now_playing_pkg
        for session in sessions:
            pkg = str(session.get("package") or "")
            title = str(session.get("title") or session.get("session_name") or pkg or "Unknown")
            artist = str(session.get("artist") or "")
            label = title if not artist else f"{title} — {artist}"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(pkg and pkg == current_pkg))
            action.triggered.connect(lambda _, p=pkg: self._select_media_player(p))
        menu.exec(self._player_switch_btn.mapToGlobal(self._player_switch_btn.rect().bottomRight()))

    def _select_media_player(self, package_name: str):
        self._active_media_pkg_pref = str(package_name or "")
        if self._active_media_pkg_pref:
            self.adb.launch_app(self._active_media_pkg_pref)
        self.refresh(force_media=True)
