"""Sync page — per-folder pause/resume toggles"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QLineEdit)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from ui.theme import (card_frame, lbl, section_label, toggle_switch,
                      divider, TEAL, CYAN, BLUE, AMBER, ROSE,
                      TEXT, TEXT_DIM, BORDER)
from backend.syncthing import Syncthing


_LAST_SYNC_STABILIZE_ATTEMPT = 0.0


class SyncRefreshWorker(QThread):
    done = pyqtSignal(object)

    def run(self):
        global _LAST_SYNC_STABILIZE_ATTEMPT
        import time
        st = Syncthing()
        status = st.get_runtime_status(timeout=3)
        service_active = bool(status.get("service_active", False))
        api_reachable = bool(status.get("api_reachable", False))
        reason = str(status.get("reason") or "unknown")
        unit_file_state = str(status.get("unit_file_state") or "unknown")

        if (not service_active) and unit_file_state != "masked" and reason in {
            "unit_inactive_api_reachable",
            "unit_inactive",
            "unit_failed",
            "service_inactive",
        }:
            now = time.time()
            if (now - _LAST_SYNC_STABILIZE_ATTEMPT) > 30.0:
                _LAST_SYNC_STABILIZE_ATTEMPT = now
                st.set_running(True)
                status = st.get_runtime_status(timeout=3)
                service_active = bool(status.get("service_active", False))
                api_reachable = bool(status.get("api_reachable", False))
                reason = str(status.get("reason") or "unknown")
                unit_file_state = str(status.get("unit_file_state") or "unknown")

        effective_connected = bool(
            (service_active and api_reachable)
            or ((not service_active) and api_reachable)
        )
        if not effective_connected:
            self.done.emit(
                {
                    "running": False,
                    "service_active": service_active,
                    "api_reachable": api_reachable,
                    "unit_file_state": unit_file_state,
                    "reason": reason,
                    "folders": [],
                    "rates": {},
                }
            )
            return
        self.done.emit({
            "running": True,
            "service_active": service_active,
            "api_reachable": api_reachable,
            "unit_file_state": unit_file_state,
            "reason": reason,
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
            service_active = bool((data or {}).get("service_active", False))
            api_reachable = bool((data or {}).get("api_reachable", False))
            unit_file_state = str((data or {}).get("unit_file_state") or "unknown")
            reason = str((data or {}).get("reason") or "unknown")
            msg = (
                "Syncthing service inactive · check systemd user unit"
                if not service_active
                else f"Syncthing service active but API unreachable ({reason})"
                if not api_reachable
                else "Syncthing unavailable"
            )
            if (not service_active) and api_reachable and unit_file_state == "masked":
                msg = "Syncthing API reachable (external instance); systemd unit is masked"
            self._folders_layout.addWidget(lbl(msg, 12, TEXT_DIM))
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
        state_text = "Paused" if f.get("paused") else f["state"].replace("_", " ").title()
        badge = QLabel(state_text)
        badge.setStyleSheet(f"""
            QLabel {{
                color:{color}; background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.14); border-radius:99px;
                padding:3px 10px; font-size:10px; font-family:monospace;
                font-weight:600;
            }}
        """)
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
        path_input.setReadOnly(True)
        path_input.setStyleSheet(f"""
            QLineEdit {{
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.07);
                border-radius:8px;color:rgba(255,255,255,0.6);
                padding:6px 10px;font-size:11px;font-family:monospace;
            }}
            QLineEdit:focus {{ border-color:rgba(167,139,250,0.3); }}
        """)
        update_btn = QPushButton("Edit")
        update_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(167,139,250,0.08);border:1px solid rgba(167,139,250,0.2);
                border-radius:8px;color:{TEAL};padding:6px 12px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
        """)
        update_btn.clicked.connect(lambda _, fid=folder_id: self._toggle_edit_path(fid))

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
            "path_input": path_input,
            "edit_btn": update_btn,
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

        state_text = "Paused" if f.get("paused") else f["state"].replace("_", " ").title()
        refs["badge"].setText(state_text)
        refs["badge"].setStyleSheet(f"""
            QLabel {{
                color:{color}; background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.14); border-radius:99px;
                padding:3px 10px; font-size:10px; font-family:monospace;
                font-weight:600;
            }}
        """)

        gb  = f.get("globalBytes",1) or 1
        ib  = f.get("inSyncBytes",0)
        pct = min(100, int((ib/gb)*100))
        self._set_bar(folder_id, pct, color,
                      refs["bar_bg"], refs["bar_fill"])
        path_input = refs.get("path_input")
        if path_input is not None and path_input.isReadOnly():
            path_input.setText(str(f.get("path", "")))

    def _toggle_edit_path(self, folder_id):
        refs = self._folder_rows.get(folder_id)
        if not refs:
            return
        path_input = refs.get("path_input")
        edit_btn = refs.get("edit_btn")
        if path_input is None or edit_btn is None:
            return
        if path_input.isReadOnly():
            path_input.setReadOnly(False)
            path_input.setFocus()
            edit_btn.setText("Save")
            return
        new_path = path_input.text().strip()
        ok = self.st.update_folder_path(folder_id, new_path)
        if ok:
            path_input.setReadOnly(True)
            edit_btn.setText("Edit")
            QTimer.singleShot(120, self.refresh)
            return
        edit_btn.setText("Retry")
