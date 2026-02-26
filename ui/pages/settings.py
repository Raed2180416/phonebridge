"""Settings page"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QLineEdit, QComboBox)
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      toggle_switch, divider, ToggleRow, InfoRow,
                      TEAL, CYAN, VIOLET, ROSE, AMBER, TEXT, TEXT_DIM, BORDER)
import backend.settings_store as settings

class SettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self._build()

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
        dl.addWidget(InfoRow("📱","Device Name","",settings.get("device_name",""), clickable=False))
        dl.addWidget(divider())
        dl.addWidget(InfoRow("🌐","Phone Tailscale IP","",settings.get("phone_tailscale_ip",""),clickable=False))
        dl.addWidget(divider())
        dl.addWidget(InfoRow("🔗","NixOS Tailscale IP","",settings.get("nixos_tailscale_ip",""),clickable=False))
        dl.addWidget(divider())
        dl.addWidget(InfoRow("🔑","KDE Connect ID","",settings.get("device_id","")[:16]+"…",clickable=False))
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
                    background:rgba(62,240,176,0.08);border:1px solid rgba(62,240,176,0.2);
                    border-radius:8px;color:{TEAL};padding:7px 12px;font-size:11px;
                }}
                QPushButton:hover {{ background:rgba(62,240,176,0.18); }}
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

        rich_motion = ToggleRow("🎞", "Rich Animations",
                              "Use smooth transitions for page/dialog interactions",
                              checked=settings.get("motion_level", "rich") == "rich")
        rich_motion.toggled.connect(self._set_motion_level)
        bl.addWidget(rich_motion)
        bl.addWidget(divider())

        sync_data = ToggleRow("📶", "Sync on Mobile Data",
                              "Allow Syncthing over cellular",
                              checked=False)
        bl.addWidget(sync_data)
        layout.addWidget(behav_frame)

        # ── System ───────────────────────────────────────────────
        layout.addWidget(section_label("System"))
        sys_frame = card_frame()
        sl = QVBoxLayout(sys_frame)
        sl.setContentsMargins(0,8,0,8)
        sl.setSpacing(0)

        startup = ToggleRow("🚀","Start on Login","Run as systemd user service",checked=True)
        sl.addWidget(startup)
        sl.addWidget(divider())
        startup_check = ToggleRow("🔔","Startup Connectivity Check",
                                   "Show popout on login",checked=True)
        sl.addWidget(startup_check)
        sl.addWidget(divider())
        close_mode = ToggleRow("🗕","Close to Tray",
                               "When window is closed, minimize to tray instead of quitting",
                               checked=settings.get("close_to_tray", True))
        close_mode.toggled.connect(lambda v: settings.set("close_to_tray", v))
        sl.addWidget(close_mode)
        sl.addWidget(divider())
        sl.addWidget(InfoRow("⌨️","Toggle Keybind","Show/hide window",
                              "Super + P",clickable=False))
        layout.addWidget(sys_frame)

        # ── Appearance ───────────────────────────────────────────
        layout.addWidget(section_label("Appearance"))
        app_frame = card_frame()
        apl = QVBoxLayout(app_frame)
        apl.setContentsMargins(20,14,20,14)
        apl.setSpacing(10)

        theme_row = QHBoxLayout()
        theme_row.addWidget(lbl("Theme", 12, bold=True))
        theme_row.addStretch()
        self._theme_combo = QComboBox()
        self._theme_combo.setStyleSheet(f"""
            QComboBox {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.12);
                border-radius:8px;color:{TEXT};padding:6px 10px;font-size:11px;
            }}
        """)
        self._theme_combo.addItem("Slate", "slate")
        self._theme_combo.addItem("Mist", "mist")
        self._theme_combo.addItem("Night", "night")
        current_theme = str(settings.get("theme_name", "slate") or "slate")
        idx = max(0, self._theme_combo.findData(current_theme))
        self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.currentIndexChanged.connect(self._set_theme)
        theme_row.addWidget(self._theme_combo)
        apl.addLayout(theme_row)
        apl.addWidget(lbl("Theme updates colors and accents across the app.", 10, TEXT_DIM))
        layout.addWidget(app_frame)

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

    def _set_theme(self):
        theme = self._theme_combo.currentData() or "slate"
        settings.set("theme_name", theme)
        win = self.window()
        if win and hasattr(win, "apply_visual_settings"):
            win.apply_visual_settings(theme_name=theme)

    def _set_motion_level(self, enabled):
        level = "rich" if bool(enabled) else "subtle"
        settings.set("motion_level", level)
        win = self.window()
        if win and hasattr(win, "apply_visual_settings"):
            win.apply_visual_settings(motion_level=level)

    def _force_kill(self):
        import os
        os._exit(0)
