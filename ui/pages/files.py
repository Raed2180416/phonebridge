"""Files page — folder browser + send files to phone."""
import logging
import os
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import backend.settings_store as settings
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      divider, with_alpha, TEAL, CYAN, VIOLET, ROSE, AMBER, BLUE,
                      TEXT, TEXT_DIM, TEXT_MID, BORDER)
from backend.kdeconnect import KDEConnect
from backend.syncthing import Syncthing
from ui.pages.files_backend import (
    DEFAULT_FOLDERS,
    FilesLoadWorker,
    FilesMutationWorker,
    list_entries_payload,
)
from ui.pages.files_actions import FilesActionsMixin

log = logging.getLogger(__name__)

class FilesPage(FilesActionsMixin, QWidget):
    allow_periodic_refresh = False
    allow_runtime_status_refresh = False

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc = KDEConnect()
        self.st = Syncthing()
        self._current_folder = None
        self._entry_limits = {}
        self._folders = []
        self._load_worker: FilesLoadWorker | None = None
        self._load_workers: set[FilesLoadWorker] = set()
        self._load_token = 0
        self._mutation_worker: FilesMutationWorker | None = None
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(24,24,24,24)
        self._main_layout.setSpacing(14)
        self.destroyed.connect(lambda *_args: self._cancel_load())
        self._show_loading("Loading files…")
        self.refresh()

    def _cancel_load(self):
        for worker in list(self._load_workers):
            try:
                worker.cancel()
            except Exception:
                pass
        self._load_worker = None

    def _finish_load_worker(self, worker: FilesLoadWorker):
        self._load_workers.discard(worker)
        if self._load_worker is worker:
            self._load_worker = None

    def _start_load(self, *, mode: str, folder: dict | None = None):
        self._cancel_load()
        self._load_token += 1
        folder_id = str((folder or {}).get("id") or "")
        log.info("Files page load start mode=%s token=%s folder_id=%s", mode, self._load_token, folder_id)
        worker = FilesLoadWorker(
            token=self._load_token,
            mode=mode,
            folder=folder,
            limit=self._entry_limits.get(str((folder or {}).get("id") or ""), 72),
        )
        worker.done.connect(self._apply_loaded_payload)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda _ok=True, w=worker: self._finish_load_worker(w))
        self._load_workers.add(worker)
        self._load_worker = worker
        worker.start()

    def _show_loading(self, message: str):
        self._clear_main_layout()
        self._main_layout.addWidget(lbl("File Browser", 22, bold=True))
        self._main_layout.addWidget(lbl(str(message or "Loading…"), 12, TEXT_DIM))
        self._main_layout.addStretch()

    def _apply_loaded_payload(self, payload):
        if not isinstance(payload, dict):
            return
        if int(payload.get("token") or 0) != self._load_token:
            return
        if bool(payload.get("cancelled")):
            return
        if payload.get("mode") == "grid":
            self._folders = list(payload.get("folders") or [])
            self._current_folder = None
            log.info("Files page loaded grid token=%s folders=%s", payload.get("token"), len(self._folders))
            self._build_folder_grid()
            return
        folder = dict(payload.get("folder") or self._current_folder or {})
        self._current_folder = folder
        entries = list(payload.get("entries") or [])
        thumb_count = sum(1 for row in entries if str((row or {}).get("thumb_path") or "").strip())
        log.info(
            "Files page loaded folder token=%s folder_id=%s entries=%s thumbs=%s truncated=%s",
            payload.get("token"),
            folder.get("id"),
            len(entries),
            thumb_count,
            bool(payload.get("truncated", False)),
        )
        self._clear_main_layout()
        self._main_layout.addWidget(
            self._file_view(
                folder,
                entries=entries,
                truncated=bool(payload.get("truncated", False)),
            )
        )
        self._main_layout.addStretch()

    def _save_folders(self):
        overrides = {}
        custom = []
        default_ids = {f["id"] for f in DEFAULT_FOLDERS}
        default_paths = {f["id"]: f["path"] for f in DEFAULT_FOLDERS}
        for folder in self._folders:
            fid = folder.get("id")
            if fid in default_ids:
                base = default_paths.get(fid)
                path = folder.get("path")
                if base and path and path != base:
                    overrides[fid] = path
            elif folder.get("custom"):
                custom.append({
                    "id": fid,
                    "icon": folder.get("icon", "📁"),
                    "name": folder.get("name", "Folder"),
                    "path": folder.get("path", ""),
                    "synced": bool(folder.get("synced", False)),
                    "color": folder.get("color", BLUE),
                    "syncthing_id": folder.get("syncthing_id", ""),
                })
        settings.set_many({
            "folder_overrides": overrides,
            "custom_folders": custom,
        })

    def _folder_by_id(self, folder_id: str):
        fid = str(folder_id or "")
        for row in self._folders:
            if str(row.get("id") or "") == fid:
                return row
        return None

    def _update_folder_state(self, folder_id: str, **updates):
        row = self._folder_by_id(folder_id)
        if row is not None:
            row.update(updates)
        if self._current_folder and str(self._current_folder.get("id") or "") == str(folder_id or ""):
            self._current_folder.update(updates)

    def _finish_mutation(self):
        self._mutation_worker = None

    def _start_mutation(self, *, mode: str, folder: dict, callback) -> bool:
        if self._mutation_worker is not None and self._mutation_worker.isRunning():
            QMessageBox.information(self, "PhoneBridge", "Please wait for the current folder action to finish.")
            return False
        worker = FilesMutationWorker(mode=mode, folder=folder)
        worker.done.connect(callback)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._finish_mutation)
        self._mutation_worker = worker
        worker.start()
        return True

    def _build_folder_grid(self):
        self._clear_main_layout()

        self._title_lbl = lbl("File Browser", 22, bold=True)
        self._main_layout.addWidget(self._title_lbl)

        guide = card_frame()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.setSpacing(4)
        gl.addWidget(section_label("Flow"))
        gl.addWidget(lbl("1) Pick a folder card  2) Browse and preview files  3) Open or send to phone", 11, TEXT_DIM))
        self._main_layout.addWidget(guide)

        # Send files card
        send_frame = card_frame(accent=True)
        sl = QHBoxLayout(send_frame)
        sl.setContentsMargins(20,14,20,14)
        sl.setSpacing(12)
        sl.addWidget(lbl("📤", 22))
        info = QVBoxLayout()
        info.addWidget(lbl("Send files to phone", 13, bold=True))
        info.addWidget(lbl("Via KDE Connect Share plugin", 11, TEXT_DIM))
        sl.addLayout(info)
        sl.addStretch()
        send_btn = action_btn("Send Files", TEAL)
        send_btn.clicked.connect(self._send_files)
        sl.addWidget(send_btn)
        drag_btn = action_btn("Share Text", CYAN)
        drag_btn.clicked.connect(self._send_text)
        sl.addWidget(drag_btn)
        add_folder_btn = action_btn("Add Location", VIOLET)
        add_folder_btn.clicked.connect(self._add_custom_folder)
        sl.addWidget(add_folder_btn)
        self._main_layout.addWidget(send_frame)

        # Folder grid
        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background:transparent;")
        grid = QGridLayout(self._grid_widget)
        grid.setSpacing(12)
        grid.setContentsMargins(0,0,0,0)

        for i, folder in enumerate(self._folders):
            grid.addWidget(self._folder_card(folder), i//3, i%3)

        self._main_layout.addWidget(self._grid_widget)
        self._main_layout.addStretch()

    def _clear_main_layout(self):
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _folder_card(self, folder):
        color = folder.get("color", TEAL)
        f = QFrame()
        f.setStyleSheet(f"""
            QFrame {{
                background: rgba(255,255,255,0.035);
                border: 1px solid rgba(255,255,255,0.07);
                border-radius: 16px;
            }}
            QFrame:hover {{
                background: rgba(255,255,255,0.06);
                border-color: {with_alpha(color, 0.28)};
            }}
        """)
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        fl = QVBoxLayout(f)
        fl.setContentsMargins(16,16,16,16)
        fl.setSpacing(8)
        fl.addWidget(lbl(folder["icon"], 28))
        fl.addWidget(lbl(folder["name"], 13, bold=True))

        path_lbl = lbl(folder["path"].replace(os.path.expanduser("~"),"~"),
                       9, TEXT_DIM, mono=True)
        fl.addWidget(path_lbl)

        status_row = QHBoxLayout()
        pip = QFrame()
        pip.setFixedSize(7,7)
        synced = bool(folder.get("synced", False))
        folder["synced"] = synced
        pip_color = TEAL if synced else AMBER
        pip.setStyleSheet(f"""
            QFrame {{
                background:{pip_color};
                border-radius:3px;
                border:none;
            }}
        """)
        status_row.addWidget(pip)
        status_row.addWidget(lbl("Synced" if synced else "Paused", 10,
                                  pip_color if synced else AMBER))
        status_row.addStretch()
        fl.addLayout(status_row)

        folder_data = folder
        f.mousePressEvent = lambda e, fd=folder_data: self._open_folder(fd)
        return f

    def _open_folder(self, folder):
        self._current_folder = folder
        fid = folder.get("id")
        if fid and fid not in self._entry_limits:
            self._entry_limits[fid] = 72
        log.info("Files page opening folder folder_id=%s path=%s", fid, folder.get("path", ""))
        self._show_loading(f"Loading {folder.get('name', 'folder')}…")
        self._start_load(mode="folder", folder=folder)

    def _file_view(self, folder, *, entries=None, truncated=False):
        frame = card_frame()
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(20,16,20,16)
        fl.setSpacing(12)

        # Header with back button
        hdr = QHBoxLayout()
        back = QPushButton("← Folders")
        back.setStyleSheet(f"""
            QPushButton {{
                background:rgba(255,255,255,0.05);
                border:1px solid rgba(255,255,255,0.1);
                border-radius:8px;color:{TEXT_DIM};
                padding:6px 12px;font-size:11px;font-family:monospace;
            }}
            QPushButton:hover {{ background:rgba(255,255,255,0.1);color:white; }}
        """)
        back.clicked.connect(self._back_to_grid)
        hdr.addWidget(back)
        hdr.addWidget(lbl(f"📱 / {folder['name']}", 12, TEXT_DIM, mono=True))
        hdr.addStretch()

        open_btn = action_btn("Open in Files", CYAN)
        open_btn.clicked.connect(lambda: self._open_path(folder["path"]))
        hdr.addWidget(open_btn)
        mkdir_btn = action_btn("New Folder", VIOLET)
        mkdir_btn.clicked.connect(lambda: self._create_subfolder(folder))
        hdr.addWidget(mkdir_btn)
        sync_btn = action_btn("Sync Folder", TEAL)
        sync_btn.clicked.connect(lambda: self._toggle_syncthing_sync(folder, sync_btn))
        hdr.addWidget(sync_btn)
        if folder.get("custom"):
            remove_btn = action_btn("Remove Card", ROSE)
            remove_btn.clicked.connect(lambda: self._remove_custom_folder(folder))
            hdr.addWidget(remove_btn)
            delete_btn = action_btn("Delete Folder", ROSE)
            delete_btn.clicked.connect(lambda: self._delete_physical_folder(folder))
            hdr.addWidget(delete_btn)
        fl.addLayout(hdr)
        self._refresh_sync_btn(folder, sync_btn)

        # Folder path editor
        path_row = QHBoxLayout()
        path_row.addWidget(lbl("PC Path", 10, TEXT_DIM, mono=True))
        path_input = input_field(folder["path"])
        path_input.setText(folder["path"])
        set_btn = QPushButton("Set")
        set_btn.setFixedWidth(50)
        set_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(167,139,250,0.08);border:1px solid rgba(167,139,250,0.2);
                border-radius:8px;color:{TEAL};padding:7px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
        """)
        set_btn.clicked.connect(lambda: self._set_folder_path(folder, path_input.text().strip()))
        path_row.addWidget(path_input)
        path_row.addWidget(set_btn)
        fl.addLayout(path_row)

        fl.addWidget(divider())

        # File list
        entries = list(entries or [])

        if not entries:
            fl.addWidget(lbl("No files found — folder may be empty or not synced yet",
                             12, TEXT_DIM))
        else:
            for idx, row in enumerate(entries):
                fl.addWidget(self._file_row(
                    row["name"],
                    row["full"],
                    size=row.get("size"),
                    thumb_path=row.get("thumb_path"),
                    allow_thumbnail=(idx < 36),
                ))
            if truncated:
                more_btn = action_btn("Load More", CYAN)
                more_btn.clicked.connect(lambda: self._load_more_entries(folder))
                fl.addWidget(more_btn)

        return frame

    def _file_row(self, name, full_path, *, size=None, thumb_path=None, allow_thumbnail=True):
        ext  = name.rsplit(".",1)[-1].lower() if "." in name else ""
        ico  = {"jpg":"🖼️","jpeg":"🖼️","png":"🖼️","gif":"🖼️",
                "mp4":"🎥","mov":"🎥","mp3":"🎵","opus":"🎵",
                "pdf":"📄","docx":"📄","xlsx":"📊","txt":"📝",
                "apk":"📦","zip":"📦"}.get(ext,"📁")

        try:
            actual_size = int(size if size is not None else os.path.getsize(full_path))
            size_str = f"{actual_size/1024/1024:.1f} MB" if actual_size > 1024*1024 else f"{actual_size/1024:.0f} KB"
        except Exception:
            size_str = "—"

        row = QFrame()
        row.setStyleSheet("""
            QFrame {
                background:rgba(255,255,255,0.025);
                border:1px solid transparent;border-radius:10px;
            }
            QFrame:hover {
                background:rgba(255,255,255,0.05);
                border-color:rgba(255,255,255,0.07);
            }
        """)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12,9,12,9)
        rl.setSpacing(12)

        thumb = self._thumbnail_label(
            full_path,
            ext,
            ico,
            allow_thumbnail=allow_thumbnail,
            thumb_path=thumb_path,
        )
        rl.addWidget(thumb)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(lbl(name, 12, bold=True))
        info.addWidget(lbl(size_str, 10, TEXT_DIM, mono=True))
        rl.addLayout(info)
        rl.addStretch()

        open_btn = QPushButton("Open")
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.1);
                border-radius:7px;color:{TEXT_DIM};padding:5px 10px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(167,139,250,0.1);color:{TEAL};border-color:rgba(167,139,250,0.25); }}
        """)
        fp = full_path
        open_btn.clicked.connect(lambda _, p=fp: self._open_path(p))
        rl.addWidget(open_btn)

        send_btn = QPushButton("Send →")
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(167,139,250,0.07);border:1px solid rgba(167,139,250,0.2);
                border-radius:7px;color:{TEAL};padding:5px 10px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(167,139,250,0.18); }}
        """)
        fp2 = full_path
        send_btn.clicked.connect(lambda _, p=fp2: self.kc.share_file(p))
        rl.addWidget(send_btn)

        row.mousePressEvent = lambda e, p=fp: self._open_path(p)
        return row

    def _back_to_grid(self):
        self._current_folder = None
        self._show_loading("Loading files…")
        self._start_load(mode="grid")

    def _load_more_entries(self, folder):
        fid = folder.get("id")
        if not fid:
            return
        self._entry_limits[fid] = self._entry_limits.get(fid, 72) + 72
        self._open_folder(folder)

    def _send_files(self):
        files = self._pick_files("Select files to send to phone", os.path.expanduser("~"))
        for f in files:
            self.kc.share_file(f)

    def _send_text(self):
        from PyQt6.QtWidgets import QDialog
        d = QDialog(self)
        d.setWindowTitle("Send Text to Phone")
        d.setStyleSheet("background:#070c17;color:white;")
        d.resize(380,200)
        lay = QVBoxLayout(d)
        lay.addWidget(lbl("Text to send:", 13))
        from ui.theme import text_area
        ta = text_area("Enter text…", 100)
        lay.addWidget(ta)
        send = action_btn("Send to Phone", TEAL)
        send.clicked.connect(lambda: (self.kc.share_text(ta.toPlainText()), d.close()))
        lay.addWidget(send)
        d.exec()

    def _open_path(self, path):
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception as e:
            log.warning("Failed to open path %s: %s", path, e)

    def _list_entries(self, path, limit=120):
        return list_entries_payload(path, limit=limit)

    def _set_folder_path(self, folder, new_path):
        if not new_path:
            return
        folder["path"] = new_path
        self._save_folders()
        if self._current_folder and self._current_folder.get("id") == folder.get("id"):
            self._open_folder(folder)
        else:
            self.refresh()

    def _create_subfolder(self, folder):
        base = folder.get("path", "")
        if not base:
            return
        name = self._pick_text("New Folder", "Folder name:", "")
        if not name:
            return
        target = os.path.join(base, name)
        try:
            os.makedirs(target, exist_ok=True)
            self._open_folder(folder)
        except Exception as e:
            log.warning("Create folder failed %s: %s", target, e)

    def refresh(self):
        if self._current_folder:
            self._show_loading(f"Loading {self._current_folder.get('name', 'folder')}…")
            self._start_load(mode="folder", folder=self._current_folder)
            return
        self._show_loading("Loading files…")
        self._start_load(mode="grid")

    def _thumbnail_label(self, full_path, ext, fallback_ico, *, allow_thumbnail=True, thumb_path=None):
        thumb = QLabel(fallback_ico)
        thumb.setFixedSize(36, 36)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background:rgba(255,255,255,0.07);border-radius:9px;font-size:18px;border:none;")

        if not allow_thumbnail:
            return thumb

        if thumb_path and os.path.exists(thumb_path):
            pix = QPixmap(thumb_path)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation))
                thumb.setText("")
            return thumb

        return thumb
