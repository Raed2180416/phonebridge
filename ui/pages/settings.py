"""Settings page"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QComboBox, QSlider)
from PyQt6.QtCore import Qt, QTimer
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      divider, ToggleRow, InfoRow,
                      TEAL, CYAN, ROSE, TEXT, TEXT_DIM)
import backend.settings_store as settings
from backend import call_audio
from backend import autostart
from backend import runtime_config
from backend.state import state
from backend.ui_feedback import push_toast

class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self._build()
        # Refresh live volume readout when a call route becomes active/inactive
        state.subscribe("call_audio_active", self._on_call_route_state_changed, owner=self)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(14)
        layout.addWidget(lbl("Settings", 22, bold=True))

        # ── Device ────────────────────────────────────────────────
        layout.addWidget(section_label("Device"))
        dev_frame = card_frame()
        dl = QVBoxLayout(dev_frame)
        dl.setContentsMargins(0,8,0,8)
        dl.setSpacing(0)
        dl.addWidget(InfoRow("📱","Device Name","",runtime_config.device_name(), clickable=False))
        dl.addWidget(divider())
        dl.addWidget(InfoRow("🌐","Phone Tailscale IP","",runtime_config.phone_tailscale_ip(),clickable=False))
        dl.addWidget(divider())
        dl.addWidget(InfoRow("🔗","NixOS Tailscale IP","",runtime_config.host_tailscale_ip(),clickable=False))
        dl.addWidget(divider())
        device_id = runtime_config.device_id()
        display_id = device_id[:16] + "…" if len(device_id) > 16 else device_id
        dl.addWidget(InfoRow("🔑","KDE Connect ID","",display_id,clickable=False))
        layout.addWidget(dev_frame)

        # ── File paths ────────────────────────────────────────────
        layout.addWidget(section_label("File Paths"))
        paths_frame = card_frame()
        pl = QVBoxLayout(paths_frame)
        pl.setContentsMargins(20,14,20,14)
        pl.setSpacing(10)

        for label, key in [
            ("Sync Root", "sync_root"),
            ("PhoneSend Dir", "phonesend_dir"),
        ]:
            row = QHBoxLayout()
            row.addWidget(lbl(label, 12, bold=True))
            row.addStretch()
            inp = input_field(settings.get(key,""))
            inp.setText(settings.get(key,""))
            inp.setFixedWidth(280)
            save = QPushButton("Save")
            save.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(167,139,250,0.08);border:1px solid rgba(167,139,250,0.2);
                    border-radius:8px;color:{TEAL};padding:7px 12px;font-size:11px;
                }}
                QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
            """)
            k = key
            i = inp
            save.clicked.connect(lambda _, k=k, i=i: settings.set(k, i.text()))
            row.addWidget(inp)
            row.addWidget(save)
            pl.addLayout(row)

        layout.addWidget(paths_frame)

        # ── Behavior ─────────────────────────────────────────────
        layout.addWidget(section_label("Behavior"))
        behav_frame = card_frame()
        bl = QVBoxLayout(behav_frame)
        bl.setContentsMargins(0,8,0,8)
        bl.setSpacing(0)

        suppress = ToggleRow("🔕", "Suppress Call Popups",
                              "Don't show incoming call dialogs",
                              checked=settings.get("suppress_calls",False))
        suppress.toggled.connect(lambda v: settings.set("suppress_calls", v))
        bl.addWidget(suppress)
        bl.addWidget(divider())

        autoshare = ToggleRow("📋", "Auto-Share Clipboard",
                              "Phone clipboard → NixOS automatically",
                              checked=settings.get("clipboard_autoshare",True))
        autoshare.toggled.connect(lambda v: settings.set("clipboard_autoshare", v))
        bl.addWidget(autoshare)
        bl.addWidget(divider())

        auto_bt = ToggleRow("🦷", "Auto Connect Phone Bluetooth",
                             "Attempt to connect phone headset profile automatically",
                             checked=settings.get("auto_bt_connect", True))
        auto_bt.toggled.connect(lambda v: settings.set("auto_bt_connect", v))
        bl.addWidget(auto_bt)
        bl.addWidget(divider())

        sync_data = ToggleRow("📶", "Sync on Mobile Data",
                              "Pause local Syncthing folders while phone is on mobile data",
                              checked=settings.get("sync_on_mobile_data", False))
        sync_data.toggled.connect(self._set_sync_on_mobile_data)
        bl.addWidget(sync_data)
        bl.addWidget(divider())

        missed_calls = ToggleRow(
            "📵",
            "Missed Call Popups",
            "Show a manual-dismiss popup when a call is missed",
            checked=settings.get("missed_call_popups_enabled", True),
        )
        missed_calls.toggled.connect(lambda v: settings.set("missed_call_popups_enabled", bool(v)))
        bl.addWidget(missed_calls)
        layout.addWidget(behav_frame)

        # ── Call Audio ───────────────────────────────────────────
        layout.addWidget(section_label("Call Audio (Laptop Route)"))
        call_audio_frame = card_frame()
        cal = QVBoxLayout(call_audio_frame)
        cal.setContentsMargins(20, 14, 20, 14)
        cal.setSpacing(10)
        cal.addWidget(lbl("Settings are saved immediately but only applied to system audio during active laptop-routed calls. After the call ends, audio reverts to pre-call levels.", 10, TEXT_DIM))

        output_row = QHBoxLayout()
        output_row.addWidget(lbl("Output Device", 12, bold=True))
        output_row.addStretch()
        self._call_output_combo = QComboBox()
        self._call_output_combo.setFixedWidth(360)
        self._call_output_combo.setStyleSheet(f"""
            QComboBox {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px;color:{TEXT};padding:6px 10px;font-size:11px;
            }}
        """)
        self._call_output_combo.currentIndexChanged.connect(self._on_call_output_device_changed)
        output_row.addWidget(self._call_output_combo)
        cal.addLayout(output_row)

        input_row = QHBoxLayout()
        input_row.addWidget(lbl("Input Device", 12, bold=True))
        input_row.addStretch()
        self._call_input_combo = QComboBox()
        self._call_input_combo.setFixedWidth(360)
        self._call_input_combo.setStyleSheet(f"""
            QComboBox {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px;color:{TEXT};padding:6px 10px;font-size:11px;
            }}
        """)
        self._call_input_combo.currentIndexChanged.connect(self._on_call_input_device_changed)
        input_row.addWidget(self._call_input_combo)
        cal.addLayout(input_row)

        out_vol_row = QHBoxLayout()
        out_vol_row.addWidget(lbl("Output Volume", 12, bold=True))
        out_vol_row.addStretch()
        self._call_output_vol_value = lbl("100%", 11, TEXT_DIM, mono=True)
        out_vol_row.addWidget(self._call_output_vol_value)
        cal.addLayout(out_vol_row)
        self._call_output_vol = QSlider(Qt.Orientation.Horizontal)
        self._call_output_vol.setRange(0, 200)
        self._call_output_vol.setSingleStep(1)
        self._call_output_vol.valueChanged.connect(self._on_call_output_volume_changed)
        self._call_output_vol.sliderReleased.connect(self._persist_call_output_volume)
        cal.addWidget(self._call_output_vol)

        in_vol_row = QHBoxLayout()
        in_vol_row.addWidget(lbl("Input Volume", 12, bold=True))
        in_vol_row.addStretch()
        self._call_input_vol_value = lbl("100%", 11, TEXT_DIM, mono=True)
        in_vol_row.addWidget(self._call_input_vol_value)
        cal.addLayout(in_vol_row)
        self._call_input_vol = QSlider(Qt.Orientation.Horizontal)
        self._call_input_vol.setRange(0, 200)
        self._call_input_vol.setSingleStep(1)
        self._call_input_vol.valueChanged.connect(self._on_call_input_volume_changed)
        self._call_input_vol.sliderReleased.connect(self._persist_call_input_volume)
        cal.addWidget(self._call_input_vol)

        refresh_audio_btn = action_btn("Refresh Audio Devices", CYAN)
        refresh_audio_btn.clicked.connect(self._reload_call_audio_controls)
        cal.addWidget(refresh_audio_btn)
        layout.addWidget(call_audio_frame)
        self._reload_call_audio_controls()

        # ── System ───────────────────────────────────────────────
        layout.addWidget(section_label("System"))
        sys_frame = card_frame()
        sl = QVBoxLayout(sys_frame)
        sl.setContentsMargins(0,8,0,8)
        sl.setSpacing(0)

        startup = ToggleRow(
            "🚀",
            "Start on Login",
            "Run as systemd user service",
            checked=autostart.is_enabled(),
        )
        self._startup_row = startup
        startup.toggled.connect(self._set_start_on_login)
        sl.addWidget(startup)
        sl.addWidget(divider())
        close_mode = ToggleRow("🗕","Close to Tray",
                               "When window is closed, minimize to tray instead of quitting",
                               checked=settings.get("close_to_tray", True))
        close_mode.toggled.connect(lambda v: settings.set("close_to_tray", v))
        sl.addWidget(close_mode)
        layout.addWidget(sys_frame)

        # ── About ────────────────────────────────────────────────
        layout.addWidget(section_label("About"))
        about_frame = card_frame()
        al = QVBoxLayout(about_frame)
        al.setContentsMargins(20,14,20,14)
        al.setSpacing(6)

        for k, v in [
            ("App",      "PhoneBridge v0.1.0"),
            ("Platform", "NixOS · Hyprland"),
            ("Backend",  "PyQt6 · KDE Connect · Syncthing · Tailscale"),
            ("ADB",      "scrcpy --render-driver opengl"),
        ]:
            row = QHBoxLayout()
            row.addWidget(lbl(k, 11, TEXT_DIM))
            row.addStretch()
            row.addWidget(lbl(v, 11, TEXT_DIM, mono=True))
            al.addLayout(row)

        al.addWidget(divider())

        open_syncthing = action_btn("Open Syncthing Web UI  →", CYAN)
        open_syncthing.clicked.connect(
            lambda: __import__('os').system("xdg-open http://127.0.0.1:8384"))
        al.addWidget(open_syncthing)
        force_kill = action_btn("Force Kill App", ROSE)
        force_kill.clicked.connect(self._force_kill)
        al.addWidget(force_kill)
        layout.addWidget(about_frame)
        layout.addStretch()

    def _set_start_on_login(self, enabled):
        target = bool(enabled)
        ok, msg = autostart.set_enabled(target)
        actual = autostart.is_enabled()
        self._sync_startup_toggle(actual)
        if (not ok) or (actual != target):
            push_toast(
                msg or "Could not update Start on Login",
                "warning",
                2800,
            )
            return
        push_toast(
            "Start on Login enabled" if target else "Start on Login disabled",
            "success" if target else "info",
            1700,
        )

    def _sync_startup_toggle(self, enabled):
        if not hasattr(self, "_startup_row"):
            return
        toggle = self._startup_row.toggle
        toggle.blockSignals(True)
        toggle.setChecked(bool(enabled))
        toggle.blockSignals(False)

    def _set_sync_on_mobile_data(self, enabled):
        settings.set("sync_on_mobile_data", bool(enabled))
        win = self.window()
        if win and hasattr(win, "_mobile_data_policy_tick"):
            win._mobile_data_policy_tick()

    def _force_kill(self):
        import os
        os._exit(0)

    def _on_call_route_state_changed(self, active):
        """Refresh live volume slider values when call audio route activates/deactivates."""
        if not hasattr(self, "_call_output_vol"):
            return
        # Re-read actual system volumes so slider position reflects reality.
        # Defer 300 ms to let the route fully settle before reading.
        QTimer.singleShot(300, self._sync_live_volumes)

    def _sync_live_volumes(self):
        """Read actual system volume and update sliders (non-destructive, no audio change)."""
        if not hasattr(self, "_call_output_vol"):
            return
        out_vol = call_audio.output_volume_pct()
        in_vol = call_audio.input_volume_pct()
        if out_vol is not None:
            self._call_output_vol.blockSignals(True)
            self._call_output_vol.setValue(max(0, min(200, int(out_vol))))
            self._call_output_vol.blockSignals(False)
            self._call_output_vol_value.setText(f"{int(out_vol)}%")
        if in_vol is not None:
            self._call_input_vol.blockSignals(True)
            self._call_input_vol.setValue(max(0, min(200, int(in_vol))))
            self._call_input_vol.blockSignals(False)
            self._call_input_vol_value.setText(f"{int(in_vol)}%")

    def _reload_call_audio_controls(self):
        if not hasattr(self, "_call_output_combo") or not hasattr(self, "_call_input_combo"):
            return
        outputs = call_audio.list_output_devices()
        inputs = call_audio.list_input_devices()
        selected_output = str(settings.get("call_output_device", "") or "")
        selected_input = str(settings.get("call_input_device", "") or "")

        self._call_output_combo.blockSignals(True)
        self._call_output_combo.clear()
        self._call_output_combo.addItem("System Default", "")
        for row in outputs:
            selector = str(row.get("selector") or row.get("name") or row.get("id") or "")
            name = str(row.get("name") or selector)
            desc = str(row.get("description") or name)
            is_default = bool(row.get("is_default"))
            label = desc + ("  (default)" if is_default else "")
            self._call_output_combo.addItem(label, selector)
        out_idx = self._call_output_combo.findData(selected_output)
        if out_idx < 0 and selected_output:
            for idx, row in enumerate(outputs, start=1):
                candidates = {
                    str(row.get("selector") or ""),
                    str(row.get("name") or ""),
                    str(row.get("id") or ""),
                }
                if selected_output in candidates:
                    out_idx = idx
                    break
        out_idx = max(0, out_idx)
        self._call_output_combo.setCurrentIndex(out_idx)
        self._call_output_combo.blockSignals(False)

        self._call_input_combo.blockSignals(True)
        self._call_input_combo.clear()
        self._call_input_combo.addItem("System Default", "")
        for row in inputs:
            selector = str(row.get("selector") or row.get("name") or row.get("id") or "")
            name = str(row.get("name") or selector)
            desc = str(row.get("description") or name)
            is_default = bool(row.get("is_default"))
            label = desc + ("  (default)" if is_default else "")
            self._call_input_combo.addItem(label, selector)
        in_idx = self._call_input_combo.findData(selected_input)
        if in_idx < 0 and selected_input:
            for idx, row in enumerate(inputs, start=1):
                candidates = {
                    str(row.get("selector") or ""),
                    str(row.get("name") or ""),
                    str(row.get("id") or ""),
                }
                if selected_input in candidates:
                    in_idx = idx
                    break
        in_idx = max(0, in_idx)
        self._call_input_combo.setCurrentIndex(in_idx)
        self._call_input_combo.blockSignals(False)

        out_level = int(settings.get("call_output_volume_pct", -1) or -1)
        in_level = int(settings.get("call_input_volume_pct", -1) or -1)
        if out_level < 0:
            detected = call_audio.output_volume_pct()
            out_level = detected if detected is not None else 100
        if in_level < 0:
            detected = call_audio.input_volume_pct()
            in_level = detected if detected is not None else 100
        self._call_output_vol.blockSignals(True)
        self._call_output_vol.setValue(max(0, min(200, int(out_level))))
        self._call_output_vol.blockSignals(False)
        self._call_input_vol.blockSignals(True)
        self._call_input_vol.setValue(max(0, min(200, int(in_level))))
        self._call_input_vol.blockSignals(False)
        self._call_output_vol_value.setText(f"{int(self._call_output_vol.value())}%")
        self._call_input_vol_value.setText(f"{int(self._call_input_vol.value())}%")

    def _on_call_output_device_changed(self):
        value = str(self._call_output_combo.currentData() or "")
        settings.set("call_output_device", value)
        if self._call_route_active():
            ok = call_audio.set_output_device(value, persist=False)
            if not ok:
                push_toast("Could not switch output device", "warning", 1700)

    def _on_call_input_device_changed(self):
        value = str(self._call_input_combo.currentData() or "")
        settings.set("call_input_device", value)
        if self._call_route_active():
            ok = call_audio.set_input_device(value, persist=False)
            if not ok:
                push_toast("Could not switch input device", "warning", 1700)

    def _on_call_output_volume_changed(self, value):
        self._call_output_vol_value.setText(f"{int(value)}%")
        settings.set("call_output_volume_pct", int(value))
        if self._call_route_active():
            call_audio.set_output_volume_pct(int(value), persist=False)

    def _persist_call_output_volume(self):
        settings.set("call_output_volume_pct", int(self._call_output_vol.value()))
        if self._call_route_active():
            call_audio.set_output_volume_pct(int(self._call_output_vol.value()), persist=False)

    def _on_call_input_volume_changed(self, value):
        self._call_input_vol_value.setText(f"{int(value)}%")
        settings.set("call_input_volume_pct", int(value))
        if self._call_route_active():
            call_audio.set_input_volume_pct(int(value), persist=False)

    def _persist_call_input_volume(self):
        settings.set("call_input_volume_pct", int(self._call_input_vol.value()))
        if self._call_route_active():
            call_audio.set_input_volume_pct(int(self._call_input_vol.value()), persist=False)

    @staticmethod
    def _call_route_active() -> bool:
        return bool(state.get("call_audio_active", False))
