"""Dashboard page"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QGridLayout, QFrame, QApplication, QListWidget, QListWidgetItem, QStyle, QMenu)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint, QSize
from PyQt6.QtGui import QPainter, QColor, QPixmap, QIcon, QPainterPath
import time
import os
from ui.theme import (card_frame, lbl, pill, section_label, action_btn, input_field,
                      toggle_switch, divider, TEAL, CYAN, VIOLET, ROSE,
                      with_alpha,
                      AMBER, BLUE, TEXT, TEXT_DIM, TEXT_MID, FROST, BORDER)
from backend.kdeconnect import KDEConnect
from backend.tailscale import Tailscale
from backend.adb_bridge import ADBBridge
from backend.bluetooth_manager import BluetoothManager
from backend.ui_feedback import push_toast
import backend.settings_store as settings
from backend.clipboard_history import sanitize_clipboard_history
from backend import audio_route
import backend.connectivity_controller as connectivity


_LAST_SYNCTHING_STABILIZE_ATTEMPT = 0.0


class DndWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, adb, target_state=None):
        super().__init__()
        self._adb = adb
        self._target_state = target_state

    def run(self):
        if self._target_state is None:
            self.done.emit(self._adb.get_dnd_enabled())
            return
        self._adb.toggle_dnd(bool(self._target_state))
        self.done.emit(self._adb.get_dnd_enabled())


class DashboardRefreshWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, include_media=False, preferred_media_package: str = ""):
        super().__init__()
        self._include_media = include_media
        self._preferred_media_package = str(preferred_media_package or "")

    def run(self):
        global _LAST_SYNCTHING_STABILIZE_ATTEMPT
        from backend.kdeconnect import KDEConnect
        from backend.adb_bridge import ADBBridge
        from backend.tailscale import Tailscale
        from backend.syncthing import Syncthing
        import backend.settings_store as settings
        result = {
            "battery": None,
            "network_type": None,
            "signal_strength": None,
            "media": None,
            "tailscale": False,
            "tailscale_local": False,
            "tailscale_ip": None,
            "tailscale_mesh_reason": "",
            "kde_enabled": True,
            "kde_reachable": False,
            "kde_status": "unknown",
            "syncthing": False,
            "syncthing_service_active": False,
            "syncthing_api_reachable": False,
            "syncthing_reason": "unknown",
            "syncthing_unit_file_state": "unknown",
            "wifi_enabled": None,
            "bt_enabled": None,
        }
        kc = KDEConnect()
        adb = ADBBridge()
        try:
            battery = kc.get_battery()
            if not battery or int(battery.get("charge", -1)) < 0:
                level = adb.get_battery_level()
                if level >= 0:
                    battery = {"charge": int(level), "is_charging": False, "source": "adb"}
            result["battery"] = battery
        except Exception:
            result["battery"] = None
        try:
            result["network_type"] = kc.get_network_type()
            result["signal_strength"] = kc.get_signal_strength()
        except Exception:
            result["network_type"] = None
            result["signal_strength"] = None
        try:
            ts = Tailscale()
            snapshot = ts.get_mesh_snapshot(
                phone_name=settings.get("device_name", ""),
                phone_ip=settings.get("phone_tailscale_ip", ""),
            )
            result["tailscale_local"] = bool(snapshot.get("local_connected", False))
            result["tailscale"] = bool(snapshot.get("mesh_ready", False))
            result["tailscale_mesh_reason"] = str(snapshot.get("mesh_reason") or "")
            if settings.get("tailscale_force_off", False) and result["tailscale_local"]:
                ts.down()
                snapshot = ts.get_mesh_snapshot(
                    phone_name=settings.get("device_name", ""),
                    phone_ip=settings.get("phone_tailscale_ip", ""),
                )
                result["tailscale_local"] = bool(snapshot.get("local_connected", False))
                result["tailscale"] = bool(snapshot.get("mesh_ready", False))
                result["tailscale_mesh_reason"] = str(snapshot.get("mesh_reason") or "")
            result["tailscale_ip"] = snapshot.get("self_ip")
        except Exception:
            result["tailscale"] = False
            result["tailscale_local"] = False
            result["tailscale_mesh_reason"] = "tailscale status unavailable"
            result["tailscale_ip"] = None
        try:
            result["kde_enabled"] = bool(settings.get("kde_integration_enabled", True))
            _raw = kc.is_reachable() if result["kde_enabled"] else None
            result["kde_reachable"] = _raw is True
            result["kde_status"] = (
                "disabled" if not result["kde_enabled"]
                else "reachable" if _raw is True
                else "unreachable" if _raw is False
                else "unknown"
            )
        except Exception:
            result["kde_enabled"] = bool(settings.get("kde_integration_enabled", True))
            result["kde_reachable"] = False
            result["kde_status"] = "unknown"
        try:
            st = Syncthing()
            st_status = st.get_runtime_status(timeout=3)
            reason = str(st_status.get("reason") or "")
            unit_file_state = str(st_status.get("unit_file_state") or "unknown")
            # Auto-stabilize mixed/inactive service states (throttled) so
            # dashboard doesn't stay degraded when Syncthing can be recovered.
            if reason in {"unit_inactive_api_reachable", "unit_inactive", "unit_failed", "service_inactive"} and unit_file_state != "masked":
                now = time.time()
                if (now - _LAST_SYNCTHING_STABILIZE_ATTEMPT) > 30.0:
                    _LAST_SYNCTHING_STABILIZE_ATTEMPT = now
                    st.set_running(True)
                    st_status = st.get_runtime_status(timeout=3)
            result["syncthing_service_active"] = bool(st_status.get("service_active", False))
            result["syncthing_api_reachable"] = bool(st_status.get("api_reachable", False))
            result["syncthing_reason"] = str(st_status.get("reason") or "unknown")
            result["syncthing_unit_file_state"] = str(st_status.get("unit_file_state") or "unknown")
            result["syncthing"] = bool(st_status.get("service_active", False))
        except Exception:
            result["syncthing_service_active"] = False
            result["syncthing_api_reachable"] = False
            result["syncthing_reason"] = "status_unavailable"
            result["syncthing_unit_file_state"] = "unknown"
            result["syncthing"] = False
        try:
            result["wifi_enabled"] = adb.get_wifi_enabled()
        except Exception:
            result["wifi_enabled"] = None
        try:
            result["bt_enabled"] = adb.get_bluetooth_enabled()
        except Exception:
            result["bt_enabled"] = None
        if self._include_media:
            try:
                result["media"] = adb.get_now_playing(preferred_package=self._preferred_media_package)
            except Exception:
                result["media"] = None
        self.done.emit(result)


class ToggleActionWorker(QThread):
    done = pyqtSignal(bool, str, object)

    def __init__(self, action):
        super().__init__()
        self._action = action

    def run(self):
        try:
            out = self._action()
            if isinstance(out, tuple) and len(out) >= 3:
                ok, msg, actual = out[0], out[1], out[2]
            elif isinstance(out, tuple) and len(out) == 2:
                ok, msg = out
                actual = None
            else:
                ok, msg, actual = bool(out), "", None
        except Exception as e:
            ok, msg, actual = False, str(e), None
        self.done.emit(bool(ok), str(msg or ""), actual)


class DashboardPage(QWidget):
    _EMPTY_MEDIA_TEXT = "Nothing playing right now. Play something to see it."

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc  = KDEConnect()
        self.ts  = Tailscale()
        self.adb = ADBBridge()
        self.bt  = BluetoothManager()
        self._dnd_active = settings.get("dnd_active", False)
        self._dnd_busy = False
        self._dnd_probe_busy = False
        self._last_dnd_probe = 0.0
        self._dnd_worker = None
        self._refresh_worker = None
        self._refresh_busy = False
        self._last_media_refresh = 0.0
        self._now_playing_pkg = ""
        self._toggle_worker = None
        self._play_is_playing = False
        self._media_sessions = []
        self._active_media_pkg_pref = ""
        self._build()
        from backend.state import state
        state.subscribe("audio_redirect_enabled", self._on_audio_redirect_state_changed)
        state.subscribe("connectivity_ops_busy", self._on_connectivity_ops_busy)
        self._sync_audio_route_toggle()
        self.refresh()

    def _on_audio_redirect_state_changed(self, enabled):
        if hasattr(self, "_audio_row"):
            t = getattr(self._audio_row, "_toggle", None)
            if t is not None:
                t.blockSignals(True)
                t.setChecked(bool(enabled))
                t.blockSignals(False)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(14)

        # ── Hero ─────────────────────────────────────────────────
        hero = QFrame()
        hero.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(167,139,250,0.07),
                    stop:0.5 rgba(167,139,250,0.07),
                    stop:1 rgba(59,130,246,0.05));
                border: 1px solid rgba(167,139,250,0.15);
                border-radius: 20px;
            }
        """)
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(22,18,22,18)
        hl.setSpacing(12)

        top = QHBoxLayout()
        orb = QLabel("📱")
        orb.setFixedSize(56,56)
        orb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orb.setStyleSheet("""
            QLabel {
                background: rgba(167,139,250,0.1);
                border: 1px solid rgba(167,139,250,0.22);
                border-radius: 17px;
                font-size: 26px;
            }
        """)
        info = QVBoxLayout()
        info.setSpacing(3)
        self._device_name_lbl = lbl("Phone", 20, TEXT, bold=True)
        self._device_sub_lbl  = lbl("tailnet · KDE Connect",
                                    10, TEXT_DIM, mono=True)
        info.addWidget(self._device_name_lbl)
        info.addWidget(self._device_sub_lbl)
        top.addWidget(orb)
        top.addSpacing(12)
        top.addLayout(info)
        top.addStretch()
        hl.addLayout(top)

        # Pills
        pills_row = QHBoxLayout()
        pills_row.setSpacing(8)
        self._kde_pill = pill("KDE Connect", TEXT_MID, pulse=False)
        self._ts_pill = pill("Tailscale", TEXT_MID, pulse=False)
        pills_row.addWidget(self._kde_pill)
        pills_row.addWidget(self._ts_pill)
        self._sync_pill = pill("Syncthing", TEXT_MID, pulse=False)
        pills_row.addWidget(self._sync_pill)
        pills_row.addStretch()
        hl.addLayout(pills_row)
        layout.addWidget(hero)

        # ── Stats ─────────────────────────────────────────────────
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._bat_card  = self._stat("Battery",  "…",   TEAL,   "Checking…")
        self._sig_card  = self._stat("Signal",   "…",   CYAN,   "…")
        self._net_card  = self._stat("Network",  "WiFi", VIOLET, "LAN direct")
        for c in [self._bat_card, self._sig_card, self._net_card]:
            stats.addWidget(c[0])
        layout.addLayout(stats)

        # ── Now Playing ───────────────────────────────────────────
        now_frame = card_frame()
        npl = QVBoxLayout(now_frame)
        npl.setContentsMargins(20,16,20,16)
        npl.setSpacing(10)
        np_head = QHBoxLayout()
        np_head.setSpacing(8)
        np_head.addWidget(section_label("Now Playing"))
        np_head.addStretch()
        self._player_switch_btn = QPushButton("➜")
        self._player_switch_btn.setFixedSize(26, 26)
        self._player_switch_btn.setToolTip("Switch active player")
        self._player_switch_btn.setEnabled(False)
        self._player_switch_btn.setText("•")
        self._player_switch_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 8px;
                color: {TEXT_DIM};
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background: {with_alpha(VIOLET, 0.10)};
                border-color: {with_alpha(VIOLET, 0.42)};
                color: {TEXT};
            }}
        """)
        self._player_switch_btn.clicked.connect(self._show_player_switch_menu)
        np_head.addWidget(self._player_switch_btn)
        npl.addLayout(np_head)

        np_body = QHBoxLayout()
        np_body.setSpacing(12)
        self._np_art = QLabel("♪")
        self._np_art.setFixedSize(56, 56)
        self._np_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._np_art.setStyleSheet(f"""
            QLabel {{
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 14px;
                color: {TEXT_DIM};
                font-size: 20px;
                font-weight: 700;
            }}
        """)
        np_text = QVBoxLayout()
        np_text.setSpacing(3)
        self._np_title = lbl(self._EMPTY_MEDIA_TEXT, 14, bold=True)
        self._np_sub = lbl("", 11, TEXT_DIM)
        np_text.addWidget(self._np_title)
        np_text.addWidget(self._np_sub)
        np_body.addWidget(self._np_art, 0, Qt.AlignmentFlag.AlignTop)
        np_body.addLayout(np_text, 1)
        npl.addLayout(np_body)

        np_ctrl = QHBoxLayout()
        np_ctrl.setSpacing(8)
        prev_btn = self._media_icon_btn("prev", VIOLET)
        prev_btn.clicked.connect(lambda: self._media_cmd("prev"))
        self._play_btn = self._media_icon_btn("play", VIOLET)
        self._play_btn.clicked.connect(lambda: self._media_cmd("toggle"))
        next_btn = self._media_icon_btn("next", VIOLET)
        next_btn.clicked.connect(lambda: self._media_cmd("next"))
        stop_btn = self._media_icon_btn("stop", VIOLET)
        stop_btn.clicked.connect(lambda: self._media_cmd("kill"))
        for b in (prev_btn, self._play_btn, next_btn, stop_btn):
            np_ctrl.addWidget(b)
        np_ctrl.addStretch()
        npl.addLayout(np_ctrl)
        layout.addWidget(now_frame)

        # ── Quick Actions ─────────────────────────────────────────
        actions_frame = card_frame()
        al = QVBoxLayout(actions_frame)
        al.setContentsMargins(20,16,20,18)
        al.setSpacing(12)
        al.addWidget(section_label("Quick Actions"))

        grid = QGridLayout()
        grid.setSpacing(10)

        self._dnd_btn = None
        actions = [
            ("✆", "Ring Phone",      TEAL,   self._ring),
            ("⌧", "Lock Phone",      VIOLET, self._lock_phone),
            ("☎", "Calls Panel",     CYAN,   self._go_calls),
            ("▤", "View Clipboard",  TEAL,   self._view_clipboard),
            ("⦿", "DND",             AMBER,  self._toggle_dnd),
        ]

        for i, (ico, name, color, cb) in enumerate(actions):
            btn = QPushButton(f"{ico}\n{name}")
            btn.setFixedHeight(76)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.03);
                    border: 1px solid rgba(255,255,255,0.10);
                    border-radius: 14px;
                    color: {TEXT_MID};
                    font-size: 11px;
                    padding: 8px 4px;
                    line-height: 1.4;
                }}
                QPushButton:hover {{
                    background: {with_alpha(VIOLET, 0.10)};
                    border-color: {with_alpha(VIOLET, 0.42)};
                    color: {TEXT};
                }}
                QPushButton:pressed {{
                    background: {with_alpha(VIOLET, 0.16)};
                    border-color: {with_alpha(VIOLET, 0.56)};
                }}
                QPushButton:checked {{
                    background: {with_alpha(VIOLET, 0.18)};
                    border-color: {with_alpha(VIOLET, 0.56)};
                    color: {TEXT};
                }}
            """)
            btn.clicked.connect(cb)

            if name == "DND":
                btn.setCheckable(True)
                btn.setChecked(self._dnd_active)
                self._dnd_btn = btn

            grid.addWidget(btn, i//4, i%4)

        al.addLayout(grid)
        layout.addWidget(actions_frame)

        # ── Connectivity toggles ──────────────────────────────────
        conn_frame = card_frame()
        cl = QVBoxLayout(conn_frame)
        cl.setContentsMargins(0,8,0,8)
        cl.setSpacing(0)
        self._audio_row = self._conn_row(
            "🔊",
            "Phone Audio to PC",
            "Global audio route (shared with Mirror page)",
            bool(settings.get("audio_redirect", False)),
            TEAL,
            on_toggle=self._toggle_audio_route_action,
        )
        cl.addWidget(self._audio_row)
        cl.addWidget(divider())
        self._wifi_row = self._conn_row("📡","Wi-Fi",
                                    "Phone Wi-Fi radio", True, CYAN,
                                    on_toggle=self._toggle_wifi_action)
        cl.addWidget(self._wifi_row)
        cl.addWidget(divider())
        self._bt_row = self._conn_row("🦷","Bluetooth",
                                    "Phone Bluetooth radio", True, VIOLET,
                                    on_toggle=self._toggle_bluetooth_action)
        cl.addWidget(self._bt_row)
        cl.addWidget(divider())
        self._hs_row = self._conn_row("📡","Mobile Hotspot",
                                    "Auto: USB tether (if wired) else Wi-Fi hotspot", False, CYAN,
                                    on_toggle=self._hotspot)
        cl.addWidget(self._hs_row)
        layout.addWidget(conn_frame)
        layout.addStretch()

    def _media_icon_btn(self, icon_name, color):
        b = QPushButton("")
        b.setFixedSize(42, 42)
        b.setStyleSheet(f"""
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
        b.setIconSize(QSize(18, 18))
        self._set_media_button_icon(b, icon_name)
        b.pressed.connect(lambda btn=b: self._animate_media_btn(btn, down=True))
        b.released.connect(lambda btn=b: self._animate_media_btn(btn, down=False))
        return b

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
        p = btn.pos()
        target = p + QPoint(0, 1) if down else p - QPoint(0, 1)
        anim.setStartValue(p)
        anim.setEndValue(target)
        anim.start()
        btn._press_anim = anim

    def _set_play_toggle_icon(self, media_state: str):
        state = (media_state or "").strip().lower()
        self._play_is_playing = state in {"playing", "active"}
        if hasattr(self, "_play_btn") and self._play_btn is not None:
            self._set_media_button_icon(self._play_btn, "pause" if self._play_is_playing else "play")

    def _is_valid_media_session(self, media: dict) -> bool:
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

    def _rounded_pixmap(self, pixmap: QPixmap, edge: int = 56, radius: int = 18) -> QPixmap:
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

    def _placeholder_media_art(self, media: dict | None, edge: int = 56, radius: int = 18) -> QPixmap:
        out = QPixmap(edge, edge)
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        path = QPainterPath()
        path.addRoundedRect(0, 0, edge, edge, radius, radius)
        painter.setClipPath(path)
        base = QColor(30, 42, 68)
        accent = QColor(86, 112, 176)
        painter.fillRect(0, 0, edge, edge, base)
        painter.fillRect(0, int(edge * 0.55), edge, int(edge * 0.45), accent)
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

    def _stat(self, label_text, val, color, sub):
        f = card_frame(hover=False)
        fl = QVBoxLayout(f)
        fl.setContentsMargins(16,14,16,14)
        fl.setSpacing(5)
        fl.addWidget(section_label(label_text))
        val_lbl = lbl(val, 28, color, bold=True)
        sub_lbl = lbl(sub, 11, TEXT_DIM)
        fl.addWidget(val_lbl)
        fl.addWidget(sub_lbl)
        # Progress bar
        bar_bg = QFrame()
        bar_bg.setFixedHeight(2)
        bar_bg.setStyleSheet(f"background:rgba(255,255,255,0.07);border-radius:1px;border:none;")
        bar_fill = QFrame(bar_bg)
        bar_fill.setFixedHeight(2)
        bar_fill.setStyleSheet(f"background:{color};border-radius:1px;border:none;")
        bar_fill.setFixedWidth(60)
        fl.addWidget(bar_bg)
        return f, val_lbl, sub_lbl, bar_fill

    def _conn_row(self, ico, name, sub, on, color, on_toggle=None):
        w = QWidget()
        w.setStyleSheet("background:transparent;border:none;")
        row = QHBoxLayout(w)
        row.setContentsMargins(20,11,20,11)
        row.setSpacing(12)
        row.addWidget(lbl(ico, 18))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(lbl(name, 13, TEXT, bold=True))
        sub_lbl = lbl(sub, 11, TEXT_DIM)
        info.addWidget(sub_lbl)
        row.addLayout(info)
        row.addStretch()
        t = toggle_switch(on, color)
        if on_toggle:
            t.toggled.connect(on_toggle)
        row.addWidget(t)
        w._toggle = t
        w._sub = sub_lbl
        return w

    def _set_conn_row_state(self, row, enabled, detail=None):
        if hasattr(row, "_toggle"):
            try:
                row._toggle.blockSignals(True)
                row._toggle.setChecked(bool(enabled))
                row._toggle.blockSignals(False)
            except RuntimeError:
                pass
        if detail is not None and hasattr(row, "_sub"):
            try:
                row._sub.setText(str(detail))
            except RuntimeError:
                pass

    def _set_pill(self, widget, text, color):
        if widget is None:
            return
        widget.setStyleSheet(
            f"""
            QWidget {{
                color:{color};
                background:transparent;
                border:none;
                border-radius:0px;
            }}
        """
        )
        txt = getattr(widget, "_text_label", None)
        if txt is not None:
            txt.setText(text)
            txt.setStyleSheet(
                f"font-size:10px;font-family:monospace;background:transparent;border:none;color:{color};"
            )
        dot = getattr(widget, "_dot_widget", None)
        if dot is not None:
            dot.setStyleSheet(f"background:{color};border:none;border-radius:3px;")

    def _set_status_pill(self, widget, label, state_name):
        state_map = {
            "connected": (TEXT_MID, "Connected"),
            "connecting": (TEXT_DIM, "Checking"),
            "degraded": (AMBER, "Degraded"),
            "disconnected": (TEXT_DIM, "Offline"),
        }
        color, suffix = state_map.get(state_name, state_map["disconnected"])
        self._set_pill(widget, f"{label} · {suffix}", color)

    def refresh(self, *, force_media: bool = False):
        if self._refresh_busy:
            self._probe_dnd_state_async()
            return
        include_media = bool(force_media or (time.time() - self._last_media_refresh) > 20)
        self._refresh_busy = True
        worker = DashboardRefreshWorker(
            include_media=include_media,
            preferred_media_package=self._active_media_pkg_pref,
        )
        self._refresh_worker = worker
        worker.done.connect(lambda data: self._apply_refresh(data, include_media))
        worker.finished.connect(lambda: self._on_refresh_worker_finished(worker))
        worker.start()
        self._probe_dnd_state_async()

    def _on_refresh_worker_finished(self, worker):
        if self._refresh_worker is worker:
            self._refresh_worker = None
        self._refresh_busy = False
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    @staticmethod
    def _wait_for_bool(getter, target, timeout_s=3.0, step_s=0.3):
        end = time.time() + timeout_s
        last = None
        while time.time() < end:
            last = getter()
            if last is not None and bool(last) == bool(target):
                return True
            time.sleep(step_s)
        return last is not None and bool(last) == bool(target)

    def _set_row_busy(self, row, busy):
        if hasattr(row, "_toggle"):
            try:
                row._toggle.setEnabled(not busy)
            except RuntimeError:
                pass

    def _on_connectivity_ops_busy(self, payload):
        row_map = {
            "wifi": getattr(self, "_wifi_row", None),
            "bluetooth": getattr(self, "_bt_row", None),
        }
        busy = payload or {}
        for key, row in row_map.items():
            if row is None:
                continue
            self._set_row_busy(row, bool((busy or {}).get(key, False)))

    def _run_toggle(self, row, action, fallback_label):
        if self._toggle_worker is not None:
            try:
                if self._toggle_worker.isRunning():
                    return
            except RuntimeError:
                self._toggle_worker = None
        self._set_row_busy(row, True)
        worker = ToggleActionWorker(action)
        self._toggle_worker = worker
        worker.done.connect(lambda ok, msg, actual: self._finish_toggle(row, ok, msg, actual, fallback_label))
        worker.finished.connect(lambda: self._on_toggle_worker_finished(worker))
        worker.start()

    def _on_toggle_worker_finished(self, worker):
        if self._toggle_worker is worker:
            self._toggle_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _finish_toggle(self, row, ok, msg, actual, fallback_label):
        self._set_row_busy(row, False)
        if actual is not None:
            self._set_conn_row_state(row, bool(actual))
        if ok:
            push_toast(msg or "Updated", "success", 1700)
        else:
            push_toast(msg or fallback_label, "warning", 1900)
        QTimer.singleShot(120, self.refresh)

    def _apply_refresh(self, data, include_media=False):
        self._refresh_busy = False
        tailscale_on = bool((data or {}).get("tailscale", False))
        tailscale_local_on = bool((data or {}).get("tailscale_local", tailscale_on))
        tailscale_ip = (data or {}).get("tailscale_ip") or "offline"
        tailscale_reason = str((data or {}).get("tailscale_mesh_reason") or "").strip()
        kde_enabled = bool((data or {}).get("kde_enabled", True))
        kde_reachable = bool((data or {}).get("kde_reachable", False))
        kde_status = str((data or {}).get("kde_status") or ("reachable" if kde_reachable else ("disabled" if not kde_enabled else "unreachable")))
        syncthing_service_active = bool((data or {}).get("syncthing_service_active", False))
        syncthing_api_reachable = bool((data or {}).get("syncthing_api_reachable", False))
        syncthing_reason = str((data or {}).get("syncthing_reason") or "unknown")
        syncthing_unit_file_state = str((data or {}).get("syncthing_unit_file_state") or "unknown")
        syncthing_effective_connected = bool(
            (syncthing_service_active and syncthing_api_reachable)
            or ((not syncthing_service_active) and syncthing_api_reachable)
        )

        self._set_status_pill(
            self._ts_pill,
            "Tailscale",
            "connected" if tailscale_on else ("connecting" if self._toggle_worker is not None else "disconnected"),
        )
        self._set_status_pill(
            self._kde_pill,
            "KDE Connect",
            "connected" if kde_status == "reachable"
            else "disconnected" if kde_status == "disabled"
            else "degraded" if kde_status == "unknown"
            else "connecting",
        )
        self._set_status_pill(
            self._sync_pill,
            "Syncthing",
            "connected" if syncthing_effective_connected
            else "degraded" if (syncthing_service_active or syncthing_api_reachable)
            else "disconnected",
        )
        tailscale_detail = tailscale_ip
        if tailscale_local_on and not tailscale_on and tailscale_reason:
            tailscale_detail = f"{tailscale_ip} ({tailscale_reason})"
        _kde_label = {
            "reachable": "reachable",
            "unreachable": "offline",
            "disabled": "disabled",
            "unknown": "unknown",
        }.get(kde_status, "unknown")
        self._device_sub_lbl.setText(
            f"{tailscale_detail} · KDE {_kde_label} · "
            f"Syncthing S:{'on' if syncthing_service_active else 'off'} A:{'up' if syncthing_api_reachable else 'down'}"
        )

        if syncthing_service_active and (not syncthing_api_reachable):
            self._net_card[2].setText(f"Syncthing API degraded ({syncthing_reason})")
        elif (not syncthing_service_active) and syncthing_api_reachable and syncthing_unit_file_state == "masked":
            self._net_card[2].setText("Syncthing API up (external instance)")

        bat = (data or {}).get("battery") or {}
        if bat and bat.get("charge", -1) >= 0:
            charge = int(bat.get("charge", 0))
            is_charging = bool(bat.get("is_charging"))
            suffix = " ⚡" if is_charging else "%"
            self._bat_card[1].setText(f"{charge}{suffix}")
            source = str(bat.get("source") or "kde")
            if source == "adb":
                self._bat_card[2].setText("On battery (via ADB)")
            else:
                self._bat_card[2].setText("Charging" if is_charging else "On battery")
            w = int(charge * self._bat_card[0].width() / 100) if self._bat_card[0].width() > 0 else 60
            self._bat_card[3].setFixedWidth(max(2, w))
        else:
            self._bat_card[1].setText("—")
            self._bat_card[2].setText("Unavailable")
            self._bat_card[3].setFixedWidth(8)

        net = (data or {}).get("network_type")
        strength = (data or {}).get("signal_strength")
        if net and net != "Unknown":
            self._sig_card[1].setText(str(net))
            bars = "▂▄▆█" if strength >= 4 else "▂▄▆_" if strength == 3 else "▂▄__" if strength == 2 else "▂___"
            self._sig_card[2].setText(bars)
        wifi_enabled = (data or {}).get("wifi_enabled")
        if wifi_enabled is None:
            self._set_conn_row_state(self._wifi_row, False, "Unknown (phone unreachable)")
        else:
            self._set_conn_row_state(self._wifi_row, bool(wifi_enabled), "Phone Wi-Fi radio")
        bt_enabled = (data or {}).get("bt_enabled")
        if bt_enabled is None:
            self._set_conn_row_state(self._bt_row, False, "Unknown (phone unreachable)")
        else:
            self._set_conn_row_state(self._bt_row, bool(bt_enabled), "Phone Bluetooth radio")

        self._sync_audio_route_toggle()

        if include_media:
            import time
            self._last_media_refresh = time.time()
            media = (data or {}).get("media")
            if media:
                sessions = list(media.get("sessions") or [])
                media, self._media_sessions = self._pick_display_media(media, sessions)
            if media:
                if self._active_media_pkg_pref:
                    found = any(
                        (s.get("package") or "") == self._active_media_pkg_pref for s in self._media_sessions
                    )
                    if not found:
                        self._active_media_pkg_pref = ""
                self._now_playing_pkg = media.get("package", "")
                if self._now_playing_pkg and not self._active_media_pkg_pref:
                    self._active_media_pkg_pref = self._now_playing_pkg
                title = media.get("title") or media.get("session_name") or media.get("package") or "Playing"
                artist = media.get("artist") or media.get("album") or media.get("package", "")
                media_state = (media.get("state") or "unknown").replace("_", " ")
                self._np_title.setText(title)
                self._np_sub.setText(f"{artist} · {media_state}")
                self._set_now_playing_artwork(media)
                self._set_play_toggle_icon(media.get("state") or "")
                self._sync_player_switch_button()
            else:
                self._now_playing_pkg = ""
                self._media_sessions = []
                self._np_title.setText(self._EMPTY_MEDIA_TEXT)
                self._np_sub.setText("")
                self._set_now_playing_artwork(None)
                self._set_play_toggle_icon("paused")
                self._sync_player_switch_button()

    def update_battery(self, charge, is_charging):
        suffix = " ⚡" if is_charging else "%"
        self._bat_card[1].setText(f"{charge}{suffix}")

    # ── Actions ───────────────────────────────────────────────
    def _ring(self):
        self.kc.ring()

    def _lock_phone(self):
        self.adb.lock_phone()

    def _hotspot(self, checked=True):
        target = bool(checked)

        def _action():
            return self.adb.set_hotspot_smart(target)

        self._run_toggle(self._hs_row, _action, "Hotspot toggle failed")

    def _view_clipboard(self):
        """Show synced phone clipboard timeline with search and source labels."""
        from backend.state import state
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QTextEdit,
            QPushButton,
            QHBoxLayout,
        )
        from PyQt6.QtCore import Qt
        import datetime

        history = sanitize_clipboard_history(state.get("clipboard_history", []) or [])
        current = (state.get("clipboard_text", "") or "").strip() or (QApplication.clipboard().text() or "").strip()
        d = QDialog(self)
        d.setWindowTitle("Synced Clipboard Timeline")
        d.setStyleSheet("background:#070c17;color:white;")
        d.resize(760, 380)
        lay = QVBoxLayout(d)
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
                # Backward compatibility: older rows may store ms timestamps.
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
        def copy_to_laptop():
            QApplication.clipboard().setText(te.toPlainText())
        clear_btn = action_btn("Clear History", ROSE)
        def clear_history():
            settings.set("clipboard_history", [])
            state.set("clipboard_history", [])
            history_list.clear()
        copy_btn.clicked.connect(copy_to_laptop)
        clear_btn.clicked.connect(clear_history)
        btn_row.addWidget(sync_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(clear_btn)
        lay.addLayout(btn_row)
        d.exec()

    def _go_mirror(self):
        window = self.window()
        if hasattr(window, 'go_to'):
            window.go_to("mirror")

    def _go_webcam(self):
        window = self.window()
        if hasattr(window, 'go_to'):
            window.go_to("mirror")

    def _go_calls(self):
        window = self.window()
        if hasattr(window, 'go_to'):
            window.go_to("calls")

    def _sync_audio_route_toggle(self):
        mirror_running = False
        win = self.window()
        if win and hasattr(win, "get_page"):
            mirror = win.get_page("mirror")
            if mirror and hasattr(mirror, "is_mirror_stream_running"):
                try:
                    mirror_running = bool(mirror.is_mirror_stream_running())
                except Exception:
                    mirror_running = False

        audio_route.sync(self.adb, suspend_ui_global=mirror_running)

    def _toggle_audio_route_action(self, checked=False):
        target = bool(checked)
        if not target:
            # Deterministic hard-off: clear all route sources/backends.
            audio_route.clear_all()
        else:
            audio_route.set_source("ui_global_toggle", target)

        win = self.window()
        mirror = win.get_page("mirror") if win and hasattr(win, "get_page") else None
        if mirror and hasattr(mirror, "sync_global_audio_state"):
            mirror.sync_global_audio_state(force=True, quiet=True)
        else:
            self._sync_audio_route_toggle()

        from backend.state import state
        final = state.get("audio_redirect_enabled", False)
        if final:
            push_toast("Phone audio routing enabled globally", "success", 1800)
        else:
            push_toast("Phone audio routing disabled", "info", 1800)

    def _toggle_tailscale_action(self, checked=False):
        if not hasattr(self, "_ts_row"):
            return
        target = bool(checked)

        def _action():
            return connectivity.set_tailscale(target)

        self._run_toggle(self._ts_row, _action, "Tailscale toggle failed")

    def _toggle_kde_action(self, checked=False):
        if not hasattr(self, "_kde_row"):
            return
        target = bool(checked)

        def _action():
            return connectivity.set_kde(target, window=self.window())

        self._run_toggle(self._kde_row, _action, "KDE toggle failed")

    def _toggle_wifi_action(self, checked=False):
        target = bool(checked)

        def _action():
            return connectivity.set_wifi(target, target=self.adb.target)

        self._run_toggle(self._wifi_row, _action, "Wi-Fi toggle failed")

    def _toggle_bluetooth_action(self, checked=False):
        target = bool(checked)

        def _action():
            return connectivity.set_bluetooth(target, target=self.adb.target)

        self._run_toggle(self._bt_row, _action, "Bluetooth toggle failed")

    def _toggle_dnd(self):
        if self._dnd_busy:
            return
        target = not self._dnd_active
        self._dnd_busy = True
        if self._dnd_btn:
            self._dnd_btn.setEnabled(False)
        self._dnd_worker = DndWorker(self.adb, target_state=target)
        self._dnd_worker.done.connect(lambda actual: self._finish_dnd_toggle(target if actual is None else bool(actual)))
        self._dnd_worker.finished.connect(self._dnd_worker.deleteLater)
        self._dnd_worker.start()

    def _finish_dnd_toggle(self, final_state):
        self._dnd_active = bool(final_state)
        settings.set("dnd_active", self._dnd_active)
        if self._dnd_btn:
            self._dnd_btn.setChecked(self._dnd_active)
            self._dnd_btn.setEnabled(True)
        self._dnd_busy = False

    def _sync_dnd_state(self):
        if self._dnd_busy or self._dnd_probe_busy:
            return
        actual = self.adb.get_dnd_enabled()
        if actual is None:
            return
        actual = bool(actual)
        if actual == self._dnd_active:
            return
        self._dnd_active = actual
        settings.set("dnd_active", actual)
        if self._dnd_btn:
            self._dnd_btn.setChecked(actual)

    def _probe_dnd_state_async(self):
        import time
        now = time.time()
        if self._dnd_busy or self._dnd_probe_busy:
            return
        if now - self._last_dnd_probe < 20:
            return
        self._last_dnd_probe = now
        self._dnd_probe_busy = True
        self._dnd_worker = DndWorker(self.adb, target_state=None)
        self._dnd_worker.done.connect(self._finish_dnd_probe)
        self._dnd_worker.finished.connect(self._dnd_worker.deleteLater)
        self._dnd_worker.start()

    def _finish_dnd_probe(self, actual):
        self._dnd_probe_busy = False
        if actual is None:
            return
        actual_bool = bool(actual)
        if actual_bool == self._dnd_active:
            return
        self._dnd_active = actual_bool
        settings.set("dnd_active", actual_bool)
        if self._dnd_btn:
            self._dnd_btn.setChecked(actual_bool)

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
