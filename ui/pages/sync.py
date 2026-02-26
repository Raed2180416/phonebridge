"""Sync page — per-folder pause/resume toggles"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QLineEdit)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from ui.theme import (card_frame, lbl, section_label, toggle_switch,
                      divider, TEAL, CYAN, BLUE, AMBER, ROSE,
                      TEXT, TEXT_DIM, BORDER)
from ui.motion import breathe
from backend.syncthing import Syncthing


class SyncRefreshWorker(QThread):
    done = pyqtSignal(object)

    def run(self):
        st = Syncthing()
        if not st.is_running():
            self.done.emit({"running": False, "folders": [], "rates": {}})
            return
        self.done.emit({
            "running": True,
            "folders": st.get_folders(),
            "rates": st.get_transfer_rates(),
        })


def fmt_bytes(b):
    if b > 1024**3: return f"{b/1024**3:.1f} GB"
    if b > 1024**2: return f"{b/1024**2:.1f} MB"
    if b > 1024:    return f"{b/1024:.0f} KB"
    return f"{b} B"

class SyncPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.st = Syncthing()
        self._folder_rows = {}
        self._refresh_busy = False
        self._refresh_worker = None
        self._build()

    def _build(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(24,24,24,24)
        self._layout.setSpacing(14)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(lbl("File Sync", 22, bold=True))
        hdr.addStretch()
        self._speed_lbl = lbl("↑ — · ↓ —", 11, CYAN, mono=True)
        hdr.addWidget(self._speed_lbl)
        self._layout.addLayout(hdr)

        guide = card_frame()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.setSpacing(4)
        gl.addWidget(section_label("Flow"))
        gl.addWidget(lbl("Track folder states, adjust sync paths, and pause/resume specific folders.", 11, TEXT_DIM))
        self._layout.addWidget(guide)

        self._folders_layout = QVBoxLayout()
        self._folders_layout.setSpacing(10)
        self._layout.addLayout(self._folders_layout)
        self._layout.addStretch()

        self.refresh()

    def refresh(self):
        if self._refresh_busy:
            return
        self._refresh_busy = True
        self._refresh_worker = SyncRefreshWorker()
        self._refresh_worker.done.connect(self._apply_refresh)
        self._refresh_worker.finished.connect(self._refresh_worker.deleteLater)
        self._refresh_worker.start()

    def _apply_refresh(self, data):
        self._refresh_busy = False
        if not (data or {}).get("running"):
            self._clear_folders()
            self._folders_layout.addWidget(
                lbl("Syncthing not running · check systemd service", 12, TEXT_DIM))
            self._speed_lbl.setText("↑ — · ↓ —")
            return

        folders = (data or {}).get("folders", []) or []

        # Update speed
        rates = (data or {}).get("rates", {}) or {}
        in_b  = rates.get("in_bps",  0)
        out_b = rates.get("out_bps", 0)
        self._speed_lbl.setText(f"↑ {fmt_bytes(out_b)}/s · ↓ {fmt_bytes(in_b)}/s")

        # Rebuild if folder count changed
        if len(folders) != len(self._folder_rows):
            self._clear_folders()
            for f in folders:
                row = self._make_folder_row(f)
                self._folders_layout.addWidget(row)
        else:
            # Update existing rows
            for f in folders:
                self._update_folder_row(f)

    def _clear_folders(self):
        self._folder_rows = {}
        while self._folders_layout.count():
            item = self._folders_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_folder_row(self, f):
        folder_id = f["id"]
        color = TEAL if f["state"] == "idle" else \
                BLUE  if f["state"] == "syncing" else \
                AMBER if f["state"] == "error"   else \
                ROSE  if f.get("paused") else TEAL

        frame = card_frame()
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(18,14,18,14)
        fl.setSpacing(10)

        # Top row
        top = QHBoxLayout()
        info = QVBoxLayout()
        info.setSpacing(3)
        name_lbl = lbl(f["label"], 14, bold=True)
        path_lbl = lbl(f["path"].replace("/home/raed","~"), 10, TEXT_DIM, mono=True)
        info.addWidget(name_lbl)
        info.addWidget(path_lbl)
        top.addLayout(info)
        top.addStretch()

        # Badge
        state_text = "PAUSED" if f.get("paused") else f["state"].upper()
        badge = QLabel(state_text)
        badge.setStyleSheet(f"""
            QLabel {{
                color:{color}; background:{color}14;
                border:1px solid {color}44; border-radius:99px;
                padding:2px 10px; font-size:9px; font-family:monospace;
                font-weight:600;
            }}
        """)
        if f.get("state") == "syncing":
            breathe(badge, min_opacity=0.45, max_opacity=1.0)
        top.addWidget(badge)
        fl.addLayout(top)

        # Progress bar
        gb = f.get("globalBytes", 1) or 1
        ib = f.get("inSyncBytes", 0)
        pct = min(100, int((ib / gb) * 100))

        bar_bg = QFrame()
        bar_bg.setFixedHeight(3)
        bar_bg.setStyleSheet(f"background:rgba(255,255,255,0.07);border-radius:1px;border:none;")
        bar_fill = QFrame(bar_bg)
        bar_fill.setFixedHeight(3)
        bar_fill.setStyleSheet(f"background:{color};border-radius:1px;border:none;")

        fl.addWidget(bar_bg)

        # Bottom row — path edit + pause toggle
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        path_input = QLineEdit()
        path_input.setText(f["path"])
        path_input.setStyleSheet(f"""
            QLineEdit {{
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.07);
                border-radius:8px;color:rgba(255,255,255,0.6);
                padding:6px 10px;font-size:11px;font-family:monospace;
            }}
            QLineEdit:focus {{ border-color:rgba(62,240,176,0.3); }}
        """)
        update_btn = QPushButton("Update")
        update_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(62,240,176,0.08);border:1px solid rgba(62,240,176,0.2);
                border-radius:8px;color:{TEAL};padding:6px 12px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(62,240,176,0.18); }}
        """)

        bottom.addWidget(path_input)
        bottom.addWidget(update_btn)
        bottom.addSpacing(8)

        # Pause toggle
        pause_lbl = lbl("Pause", 11, TEXT_DIM)
        pause_toggle = toggle_switch(f.get("paused", False), AMBER)
        fid = folder_id
        pause_toggle.toggled.connect(
            lambda checked, fid=fid: (
                self.st.pause_folder(fid) if checked else self.st.resume_folder(fid)
            )
        )

        bottom.addWidget(pause_lbl)
        bottom.addWidget(pause_toggle)
        fl.addLayout(bottom)

        # Store references for updates
        self._folder_rows[folder_id] = {
            "frame":  frame,
            "badge":  badge,
            "bar_fill": bar_fill,
            "bar_bg": bar_bg,
            "pct":    pct,
            "color":  color,
        }

        # Set initial bar width (deferred)
        QTimer.singleShot(50, lambda: self._set_bar(folder_id, pct, color, bar_bg, bar_fill))

        return frame

    def _set_bar(self, folder_id, pct, color, bar_bg, bar_fill):
        w = bar_bg.width()
        if w > 0:
            bar_fill.setFixedWidth(max(2, int(w * pct / 100)))

    def _update_folder_row(self, f):
        folder_id = f["id"]
        if folder_id not in self._folder_rows:
            return
        refs  = self._folder_rows[folder_id]
        color = TEAL if f["state"] == "idle" else \
                BLUE  if f["state"] == "syncing" else \
                AMBER if f.get("paused") else TEAL

        state_text = "PAUSED" if f.get("paused") else f["state"].upper()
        refs["badge"].setText(state_text)
        refs["badge"].setStyleSheet(f"""
            QLabel {{
                color:{color}; background:{color}14;
                border:1px solid {color}44; border-radius:99px;
                padding:2px 10px; font-size:9px; font-family:monospace;
                font-weight:600;
            }}
        """)
        if f.get("state") == "syncing":
            breathe(refs["badge"], min_opacity=0.45, max_opacity=1.0)
        else:
            anim = getattr(refs["badge"], "_pb_breathe_anim", None)
            if anim is not None:
                anim.stop()
                refs["badge"]._pb_breathe_anim = None
            effect = refs["badge"].graphicsEffect()
            if effect is not None:
                effect.setOpacity(1.0)

        gb  = f.get("globalBytes",1) or 1
        ib  = f.get("inSyncBytes",0)
        pct = min(100, int((ib/gb)*100))
        self._set_bar(folder_id, pct, color,
                      refs["bar_bg"], refs["bar_fill"])
