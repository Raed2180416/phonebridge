"""Dashboard page"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QGridLayout, QFrame, QApplication, QListWidget, QListWidgetItem)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from ui.theme import (card_frame, lbl, pill, section_label, action_btn, input_field,
                      toggle_switch, divider, TEAL, CYAN, VIOLET, ROSE,
                      AMBER, BLUE, TEXT, TEXT_DIM, TEXT_MID, FROST, BORDER)
from backend.kdeconnect import KDEConnect
from backend.tailscale import Tailscale
from backend.adb_bridge import ADBBridge
from backend.bluetooth_manager import BluetoothManager
from backend.ui_feedback import push_toast
import backend.settings_store as settings
from backend.clipboard_history import sanitize_clipboard_history
from backend import audio_route


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

    def __init__(self, include_media=False):
        super().__init__()
        self._include_media = include_media

    def run(self):
        from backend.kdeconnect import KDEConnect
        from backend.adb_bridge import ADBBridge
        result = {
            "battery": None,
            "network_type": None,
            "signal_strength": None,
            "media": None,
        }
        kc = KDEConnect()
        adb = ADBBridge()
        try:
            result["battery"] = kc.get_battery()
        except Exception:
            result["battery"] = None
        try:
            result["network_type"] = kc.get_network_type()
            result["signal_strength"] = kc.get_signal_strength()
        except Exception:
            result["network_type"] = None
            result["signal_strength"] = None
        if self._include_media:
            try:
                result["media"] = adb.get_now_playing()
            except Exception:
                result["media"] = None
        self.done.emit(result)


class DashboardPage(QWidget):
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
        self._build()
        self._sync_audio_route_toggle()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(14)

        # ── Hero ─────────────────────────────────────────────────
        hero = QFrame()
        hero.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(62,240,176,0.07),
                    stop:0.5 rgba(167,139,250,0.07),
                    stop:1 rgba(59,130,246,0.05));
                border: 1px solid rgba(62,240,176,0.15);
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
                background: rgba(62,240,176,0.1);
                border: 1px solid rgba(62,240,176,0.22);
                border-radius: 17px;
                font-size: 26px;
            }
        """)
        info = QVBoxLayout()
        info.setSpacing(3)
        self._device_name_lbl = lbl("Nothing Phone 3a Pro", 20, TEXT, bold=True)
        self._device_sub_lbl  = lbl("100.127.0.90 · tailnet · KDE Connect live",
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
        pills_row.addWidget(pill("KDE Connect", TEAL, pulse=True))
        pills_row.addWidget(pill("Tailscale · mesh", VIOLET, pulse=True))
        self._sync_pill = pill("Syncthing", CYAN, pulse=True)
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
        npl.addWidget(section_label("Now Playing"))

        self._np_title = lbl("Nothing playing right now", 14, bold=True)
        self._np_sub = lbl("Playback sessions from phone apps appear here", 11, TEXT_DIM)
        npl.addWidget(self._np_title)
        npl.addWidget(self._np_sub)

        np_ctrl = QHBoxLayout()
        np_ctrl.setSpacing(8)
        prev_btn = action_btn("Prev", CYAN)
        prev_btn.clicked.connect(lambda: self._media_cmd("prev"))
        play_btn = action_btn("Play/Pause", TEAL)
        play_btn.clicked.connect(lambda: self._media_cmd("toggle"))
        next_btn = action_btn("Next", CYAN)
        next_btn.clicked.connect(lambda: self._media_cmd("next"))
        stop_btn = action_btn("Kill App", ROSE)
        stop_btn.clicked.connect(lambda: self._media_cmd("kill"))
        for b in (prev_btn, play_btn, next_btn, stop_btn):
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
            ("◌", "Ring Phone",      TEAL,   self._ring),
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
                    border: 1px solid rgba(255,255,255,0.07);
                    border-radius: 14px;
                    color: rgba(255,255,255,0.60);
                    font-size: 11px;
                    padding: 8px 4px;
                    line-height: 1.4;
                }}
                QPushButton:hover {{
                    background: {TEAL}12;
                    border-color: {TEAL}44;
                    color: {TEXT};
                }}
                QPushButton:pressed {{
                    background: {TEAL}1F;
                    border-color: {TEAL}66;
                }}
                QPushButton:checked {{
                    background: {TEAL}1A;
                    border-color: {TEAL}55;
                    color: {TEAL};
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
        cl.addWidget(self._conn_row("🔗","Tailscale VPN",
                                    "100.127.0.90 · mesh active", True, TEAL))
        cl.addWidget(divider())
        cl.addWidget(self._conn_row("📶","KDE Connect",
                                    "LAN + Tailscale fallback", True, TEAL))
        cl.addWidget(divider())
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
                                    "Auto-connect phone profile when enabled", True, VIOLET,
                                    on_toggle=self._toggle_bluetooth_action)
        cl.addWidget(self._bt_row)
        cl.addWidget(divider())
        cl.addWidget(self._conn_row("📡","Mobile Hotspot",
                                    "Off · toggle via ADB", False, CYAN,
                                    on_toggle=self._hotspot))
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
        info.addWidget(lbl(sub, 11, TEXT_DIM))
        row.addLayout(info)
        row.addStretch()
        t = toggle_switch(on, color)
        if on_toggle:
            t.toggled.connect(on_toggle)
        row.addWidget(t)
        w._toggle = t
        return w

    def refresh(self):
        if self._refresh_busy:
            self._probe_dnd_state_async()
            return
        import time
        include_media = (time.time() - self._last_media_refresh) > 20
        self._refresh_busy = True
        self._refresh_worker = DashboardRefreshWorker(include_media=include_media)
        self._refresh_worker.done.connect(lambda data: self._apply_refresh(data, include_media))
        self._refresh_worker.finished.connect(self._refresh_worker.deleteLater)
        self._refresh_worker.start()
        self._probe_dnd_state_async()

    def _apply_refresh(self, data, include_media=False):
        self._refresh_busy = False
        bat = (data or {}).get("battery") or {}
        if bat and bat.get("charge", -1) >= 0:
            charge = int(bat.get("charge", 0))
            is_charging = bool(bat.get("is_charging"))
            suffix = " ⚡" if is_charging else "%"
            self._bat_card[1].setText(f"{charge}{suffix}")
            self._bat_card[2].setText("Charging" if is_charging else "On battery")
            w = int(charge * self._bat_card[0].width() / 100) if self._bat_card[0].width() > 0 else 60
            self._bat_card[3].setFixedWidth(max(2, w))

        net = (data or {}).get("network_type")
        strength = (data or {}).get("signal_strength")
        if net and net != "Unknown":
            self._sig_card[1].setText(str(net))
            bars = "▂▄▆█" if strength >= 4 else "▂▄▆_" if strength == 3 else "▂▄__" if strength == 2 else "▂___"
            self._sig_card[2].setText(bars)
        wifi_enabled = self.adb.get_wifi_enabled()
        if wifi_enabled is not None and hasattr(self, "_wifi_row"):
            t = getattr(self._wifi_row, "_toggle", None)
            if t is not None:
                t.blockSignals(True)
                t.setChecked(bool(wifi_enabled))
                t.blockSignals(False)
        bt_enabled = self.adb.get_bluetooth_enabled()
        if bt_enabled is not None and hasattr(self, "_bt_row"):
            t = getattr(self._bt_row, "_toggle", None)
            if t is not None:
                t.blockSignals(True)
                t.setChecked(bool(bt_enabled))
                t.blockSignals(False)

        self._sync_audio_route_toggle()

        if include_media:
            import time
            self._last_media_refresh = time.time()
            media = (data or {}).get("media")
            if media:
                self._now_playing_pkg = media.get("package", "")
                title = media.get("title") or media.get("session_name") or media.get("package") or "Playing"
                artist = media.get("artist") or media.get("album") or media.get("package", "")
                state = (media.get("state") or "unknown").replace("_", " ")
                self._np_title.setText(title)
                self._np_sub.setText(f"{artist} · {state}")
            else:
                self._now_playing_pkg = ""
                self._np_title.setText("Nothing playing right now")
                self._np_sub.setText("Playback sessions from phone apps appear here")

    def update_battery(self, charge, is_charging):
        suffix = " ⚡" if is_charging else "%"
        self._bat_card[1].setText(f"{charge}{suffix}")

    # ── Actions ───────────────────────────────────────────────
    def _ring(self):
        self.kc.ring()

    def _lock_phone(self):
        self.adb.lock_phone()

    def _hotspot(self, checked=True):
        if not self.adb.set_hotspot(bool(checked)) and checked:
            self.adb.open_hotspot_settings()

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
                background:rgba(62,240,176,0.16);
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
        enabled = bool(settings.get("audio_redirect", False))
        mirror_running = False
        win = self.window()
        if win and hasattr(win, "get_page"):
            mirror = win.get_page("mirror")
            if mirror and hasattr(mirror, "is_mirror_stream_running"):
                try:
                    mirror_running = bool(mirror.is_mirror_stream_running())
                except Exception:
                    mirror_running = False

        if enabled and not mirror_running:
            if not audio_route.start(self.adb):
                enabled = False
                settings.set("audio_redirect", False)
        elif not enabled:
            audio_route.stop()

        if hasattr(self, "_audio_row"):
            t = getattr(self._audio_row, "_toggle", None)
            if t is not None and t.isChecked() != enabled:
                t.blockSignals(True)
                t.setChecked(enabled)
                t.blockSignals(False)

    def _toggle_audio_route_action(self, checked=False):
        target = bool(checked)
        settings.set("audio_redirect", target)

        win = self.window()
        mirror = win.get_page("mirror") if win and hasattr(win, "get_page") else None
        if mirror and hasattr(mirror, "sync_global_audio_state"):
            # Mirror page owns runtime behavior while mirror stream is active.
            mirror.sync_global_audio_state(force=True, quiet=True)
            final = bool(settings.get("audio_redirect", False))
            if hasattr(mirror, "refresh"):
                mirror.refresh()
        else:
            final = bool(audio_route.set_enabled(target, adb=self.adb))

        self._sync_audio_route_toggle()
        if final:
            push_toast("Phone audio routing enabled globally", "success", 1800)
        else:
            push_toast("Phone audio routing disabled", "warning" if target else "info", 1800)

    def _toggle_wifi_action(self, checked=False):
        ok = self.adb.set_wifi(bool(checked))
        push_toast("Wi-Fi enabled on phone" if checked else "Wi-Fi disabled on phone", "success" if ok else "warning", 1500)

    def _toggle_bluetooth_action(self, checked=False):
        enabled = bool(checked)
        ok = self.adb.set_bluetooth(enabled)
        push_toast("Bluetooth enabled on phone" if enabled else "Bluetooth disabled on phone", "success" if ok else "warning", 1500)
        if enabled and settings.get("auto_bt_connect", True):
            hints = [
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            c_ok, msg = self.bt.auto_connect_phone(hints)
            push_toast(msg, "success" if c_ok else "warning", 1900)

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
        if action == "prev":
            self.adb.media_prev()
        elif action == "toggle":
            self.adb.media_play_pause()
        elif action == "next":
            self.adb.media_next()
        elif action == "kill":
            self.adb.stop_media_app(self._now_playing_pkg)
        QTimer.singleShot(350, self.refresh)
