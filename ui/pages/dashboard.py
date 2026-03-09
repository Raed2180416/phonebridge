"""Dashboard page."""

import time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from backend import audio_route
from backend.adb_bridge import ADBBridge
from backend.bluetooth_manager import BluetoothManager
import backend.connectivity_controller as connectivity
from backend.kdeconnect import KDEConnect
import backend.settings_store as settings
from backend.tailscale import Tailscale
from backend.ui_feedback import push_toast
from backend.state import state
from ui.pages.connectivity_widgets import (
    ToggleActionWorker,
    build_conn_row,
    set_conn_row_busy,
    set_conn_row_state,
    set_status_pill,
)
from ui.pages.dashboard_media import DashboardMediaMixin
from ui.pages.dashboard_workers import DashboardRefreshWorker, DndWorker
from ui.theme import (card_frame, lbl, pill, section_label, action_btn, input_field,
                      toggle_switch, divider, TEAL, CYAN, VIOLET, ROSE,
                      with_alpha,
                      AMBER, BLUE, TEXT, TEXT_DIM, TEXT_MID, FROST, BORDER)


class DashboardPage(DashboardMediaMixin, QWidget):
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
        state.subscribe("audio_redirect_enabled", self._on_audio_redirect_state_changed, owner=self)
        state.subscribe("connectivity_ops_busy", self._on_connectivity_ops_busy, owner=self)
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
        self._audio_row = build_conn_row(
            "🔊",
            "Phone Audio to PC",
            "Global audio route (shared with Mirror page)",
            bool(settings.get("audio_redirect", False)),
            TEAL,
            on_toggle=self._toggle_audio_route_action,
        )
        cl.addWidget(self._audio_row)
        cl.addWidget(divider())
        self._wifi_row = build_conn_row("📡","Wi-Fi",
                                        "Phone Wi-Fi radio", True, CYAN,
                                        on_toggle=self._toggle_wifi_action)
        cl.addWidget(self._wifi_row)
        cl.addWidget(divider())
        self._bt_row = build_conn_row("🦷","Bluetooth",
                                      "Phone Bluetooth radio", True, VIOLET,
                                      on_toggle=self._toggle_bluetooth_action)
        cl.addWidget(self._bt_row)
        cl.addWidget(divider())
        self._hs_row = build_conn_row("📡","Mobile Hotspot",
                                      "Auto: USB tether (if wired) else Wi-Fi hotspot", False, CYAN,
                                      on_toggle=self._hotspot)
        cl.addWidget(self._hs_row)
        layout.addWidget(conn_frame)
        layout.addStretch()

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
        set_conn_row_busy(row, busy)

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
            set_conn_row_state(row, bool(actual))
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
        kde_health = state.get("kde_health", {}) or {}
        kde_health_status = str(kde_health.get("status") or "").strip().lower()
        kde_health_reachable = kde_health.get("reachable")
        if kde_enabled:
            if kde_health_status == "ok" or kde_health_reachable is True:
                kde_status = "reachable"
            elif kde_status == "unknown" and kde_health_status == "degraded":
                kde_status = "unreachable"
        syncthing_service_active = bool((data or {}).get("syncthing_service_active", False))
        syncthing_api_reachable = bool((data or {}).get("syncthing_api_reachable", False))
        syncthing_reason = str((data or {}).get("syncthing_reason") or "unknown")
        syncthing_unit_file_state = str((data or {}).get("syncthing_unit_file_state") or "unknown")
        syncthing_effective_connected = bool(
            (syncthing_service_active and syncthing_api_reachable)
            or ((not syncthing_service_active) and syncthing_api_reachable)
        )

        set_status_pill(
            self._ts_pill,
            "Tailscale",
            "connected" if tailscale_on else ("connecting" if self._toggle_worker is not None else "disconnected"),
        )
        set_status_pill(
            self._kde_pill,
            "KDE Connect",
            "connected" if kde_status == "reachable"
            else "disconnected" if kde_status == "disabled"
            else "degraded" if kde_status == "unreachable"
            else "connecting",
        )
        set_status_pill(
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
            "unknown": "checking",
        }.get(kde_status, "unknown")
        self._device_sub_lbl.setText(
            f"{tailscale_detail} · KDE {_kde_label} · "
            f"Syncthing S:{'on' if syncthing_service_active else 'off'} A:{'up' if syncthing_api_reachable else 'down'}"
        )

        if syncthing_service_active and (not syncthing_api_reachable):
            self._net_card[2].setText(f"Syncthing API degraded ({syncthing_reason})")
        elif (not syncthing_service_active) and syncthing_api_reachable:
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
            set_conn_row_state(self._wifi_row, False, "Unknown (phone unreachable)")
        else:
            set_conn_row_state(self._wifi_row, bool(wifi_enabled), "Phone Wi-Fi radio")
        bt_enabled = (data or {}).get("bt_enabled")
        if bt_enabled is None:
            set_conn_row_state(self._bt_row, False, "Unknown (phone unreachable)")
        else:
            set_conn_row_state(self._bt_row, bool(bt_enabled), "Phone Bluetooth radio")

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
