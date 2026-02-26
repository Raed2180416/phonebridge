"""Files page — folder browser + send files to phone"""
import os
import subprocess
import logging
import hashlib
import tempfile
import shutil
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QFrame, QFileDialog,
                              QGridLayout, QInputDialog, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from ui.theme import (card_frame, lbl, section_label, action_btn, input_field,
                      divider, TEAL, CYAN, VIOLET, ROSE, AMBER, BLUE,
                      TEXT, TEXT_DIM, TEXT_MID, BORDER)
from ui.motion import breathe
from backend.kdeconnect import KDEConnect
from backend.syncthing import Syncthing
import backend.settings_store as settings

log = logging.getLogger(__name__)

SYNC_ROOT = os.path.expanduser("~/PhoneSync")

DEFAULT_FOLDERS = [
    {"icon":"📸","name":"Camera Roll",    "id":"phone-camera",
     "path":f"{SYNC_ROOT}/Camera",       "synced":True,  "color":TEAL},
    {"icon":"📄","name":"Documents",      "id":"phone-docs",
     "path":f"{SYNC_ROOT}/Documents",    "synced":True,  "color":CYAN},
    {"icon":"📥","name":"Downloads",      "id":"phone-downloads",
     "path":f"{SYNC_ROOT}/Downloads",    "synced":False, "color":AMBER},
    {"icon":"💬","name":"WhatsApp Media", "id":"phone-whatsapp",
     "path":f"{SYNC_ROOT}/WhatsApp",     "synced":True,  "color":"#25d366"},
    {"icon":"🖼️","name":"Screenshots",   "id":"phone-screenshots",
     "path":f"{SYNC_ROOT}/Screenshots",  "synced":True,  "color":VIOLET},
    {"icon":"📤","name":"PhoneSend",      "id":"phone-send",
     "path":f"{SYNC_ROOT}/PhoneSend",    "synced":True,  "color":ROSE},
    {"icon":"🎬","name":"PhoneBridge Recordings", "id":"phonebridge-recordings",
     "path":f"{SYNC_ROOT}/PhoneBridgeRecordings", "synced":False, "color":BLUE},
]

class FilesPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.kc = KDEConnect()
        self.st = Syncthing()
        self._current_folder = None
        self._entry_limits = {}
        self._folders = self._load_folders()
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(24,24,24,24)
        self._main_layout.setSpacing(14)
        self._build_folder_grid()

    def _load_folders(self):
        overrides = settings.get("folder_overrides", {}) or {}
        custom = settings.get("custom_folders", []) or []
        folders = []
        for folder in DEFAULT_FOLDERS:
            merged = dict(folder)
            fid = merged.get("id")
            if fid in overrides and overrides[fid]:
                merged["path"] = overrides[fid]
            folders.append(merged)
        for row in custom:
            if not isinstance(row, dict):
                continue
            name = (row.get("name") or "").strip()
            path = (row.get("path") or "").strip()
            if not name or not path:
                continue
            fid = (row.get("id") or f"custom-{hashlib.sha1(path.encode('utf-8')).hexdigest()[:10]}").strip()
            folders.append({
                "icon": row.get("icon") or "📁",
                "name": name,
                "id": fid,
                "path": path,
                "synced": bool(row.get("synced", False)),
                "color": row.get("color") or BLUE,
                "syncthing_id": row.get("syncthing_id", ""),
                "custom": True,
            })
        return folders

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
        settings.set("folder_overrides", overrides)
        settings.set("custom_folders", custom)

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
                border-color: {color}44;
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
        synced = folder.get("synced", False)
        pip_color = TEAL if synced else AMBER
        pip.setStyleSheet(f"""
            QFrame {{
                background:{pip_color};
                border-radius:3px;
                border:none;
            }}
        """)
        if synced:
            breathe(pip, min_opacity=0.35, max_opacity=1.0)
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
        # Clear and rebuild for file view
        self._clear_main_layout()

        self._main_layout.addWidget(self._file_view(folder))
        self._main_layout.addStretch()

    def _file_view(self, folder):
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
                background:rgba(62,240,176,0.08);border:1px solid rgba(62,240,176,0.2);
                border-radius:8px;color:{TEAL};padding:7px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(62,240,176,0.18); }}
        """)
        set_btn.clicked.connect(lambda: self._set_folder_path(folder, path_input.text().strip()))
        path_row.addWidget(path_input)
        path_row.addWidget(set_btn)
        fl.addLayout(path_row)

        fl.addWidget(divider())

        # File list
        path = folder["path"]
        fid = folder.get("id", "")
        limit = self._entry_limits.get(fid, 72)
        entries, truncated = self._list_entries(path, limit=limit)

        if not entries:
            fl.addWidget(lbl("No files found — folder may be empty or not synced yet",
                             12, TEXT_DIM))
        else:
            for idx, row in enumerate(entries):
                fl.addWidget(self._file_row(
                    row["name"],
                    row["full"],
                    allow_thumbnail=(idx < 18),
                ))
            if truncated:
                more_btn = action_btn("Load More", CYAN)
                more_btn.clicked.connect(lambda: self._load_more_entries(folder))
                fl.addWidget(more_btn)

        return frame

    def _file_row(self, name, full_path, allow_thumbnail=True):
        ext  = name.rsplit(".",1)[-1].lower() if "." in name else ""
        ico  = {"jpg":"🖼️","jpeg":"🖼️","png":"🖼️","gif":"🖼️",
                "mp4":"🎥","mov":"🎥","mp3":"🎵","opus":"🎵",
                "pdf":"📄","docx":"📄","xlsx":"📊","txt":"📝",
                "apk":"📦","zip":"📦"}.get(ext,"📁")

        try:
            size = os.path.getsize(full_path)
            size_str = f"{size/1024/1024:.1f} MB" if size > 1024*1024 else f"{size/1024:.0f} KB"
        except:
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

        thumb = self._thumbnail_label(full_path, ext, ico, allow_thumbnail=allow_thumbnail)
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
            QPushButton:hover {{ background:rgba(62,240,176,0.1);color:{TEAL};border-color:rgba(62,240,176,0.25); }}
        """)
        fp = full_path
        open_btn.clicked.connect(lambda _, p=fp: self._open_path(p))
        rl.addWidget(open_btn)

        send_btn = QPushButton("Send →")
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background:rgba(62,240,176,0.07);border:1px solid rgba(62,240,176,0.2);
                border-radius:7px;color:{TEAL};padding:5px 10px;font-size:11px;
            }}
            QPushButton:hover {{ background:rgba(62,240,176,0.18); }}
        """)
        fp2 = full_path
        send_btn.clicked.connect(lambda _, p=fp2: self.kc.share_file(p))
        rl.addWidget(send_btn)

        row.mousePressEvent = lambda e, p=fp: self._open_path(p)
        return row

    def _back_to_grid(self):
        self._current_folder = None
        self._build_folder_grid()

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
        rows = []
        truncated = False
        scan_cap = max(limit * 6, limit)
        try:
            with os.scandir(path) as it:
                for entry in it:
                    rows.append({
                        "name": entry.name,
                        "full": entry.path,
                        "is_dir": entry.is_dir(follow_symlinks=False),
                    })
                    if len(rows) >= scan_cap:
                        truncated = True
                        break
        except Exception:
            return [], False
        rows.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        if len(rows) > limit:
            rows = rows[:limit]
            truncated = True
        return rows, truncated

    def _set_folder_path(self, folder, new_path):
        if not new_path:
            return
        folder["path"] = new_path
        self._save_folders()
        if self._current_folder and self._current_folder.get("id") == folder.get("id"):
            self._open_folder(folder)

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

    def _add_custom_folder(self):
        path = self._pick_existing_dir("Choose folder", os.path.expanduser("~"))
        if not path:
            return
        default_name = os.path.basename(path.rstrip("/")) or "Custom Folder"
        name = self._pick_text("Folder Name", "Display name:", default_name)
        if not name:
            return
        fid = f"custom-{hashlib.sha1(path.encode('utf-8')).hexdigest()[:10]}"
        self._folders.append({
            "icon": "📁",
            "name": name,
            "id": fid,
            "path": path,
            "synced": False,
            "color": BLUE,
            "syncthing_id": "",
            "custom": True,
        })
        self._save_folders()
        self._build_folder_grid()

    def _folder_syncthing_id(self, folder):
        return (folder.get("syncthing_id") or folder.get("id") or "").strip()

    def _is_synced_in_syncthing(self, folder):
        fid = self._folder_syncthing_id(folder)
        if not fid:
            return False
        return self.st.get_folder(fid) is not None

    def _refresh_sync_btn(self, folder, button):
        synced = self._is_synced_in_syncthing(folder)
        folder["synced"] = synced
        if synced:
            button.setText("Unsync Folder")
        else:
            button.setText("Sync Folder")
        self._save_folders()

    def _toggle_syncthing_sync(self, folder, button):
        if not self.st.is_running():
            QMessageBox.warning(self, "Syncthing", "Syncthing is not running on this system.")
            return
        fid = self._folder_syncthing_id(folder)
        if not fid:
            return
        if self._is_synced_in_syncthing(folder):
            ok = self.st.remove_folder(fid)
            if ok:
                folder["synced"] = False
                self._refresh_sync_btn(folder, button)
            else:
                QMessageBox.warning(self, "Syncthing", "Failed to remove folder from Syncthing.")
            return
        ok, created = self.st.add_folder(
            path=folder.get("path", ""),
            label=folder.get("name", ""),
            folder_id=fid,
            folder_type="sendreceive",
        )
        if ok:
            folder["syncthing_id"] = fid if created in {"updated", fid} else str(created)
            folder["synced"] = True
            self._refresh_sync_btn(folder, button)
        else:
            QMessageBox.warning(self, "Syncthing", "Failed to add folder to Syncthing.")

    def _pick_text(self, title, prompt, default_text):
        if shutil.which("kdialog"):
            try:
                r = subprocess.run(
                    ["kdialog", "--inputbox", prompt, default_text, "--title", title],
                    capture_output=True, text=True, timeout=30,
                )
                text = (r.stdout or "").strip()
                if r.returncode == 0 and text:
                    return text
                if r.returncode != 0:
                    return ""
            except Exception:
                pass
        text, ok = QInputDialog.getText(self, title, prompt, text=default_text)
        text = (text or "").strip()
        if ok and text:
            return text
        return ""

    def _remove_custom_folder(self, folder, confirm=True):
        if not folder.get("custom"):
            return
        if confirm:
            answer = QMessageBox.question(
                self,
                "Remove Folder Card",
                f"Remove '{folder.get('name', 'Folder')}' from PhoneBridge?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        fid = self._folder_syncthing_id(folder)
        if fid and self._is_synced_in_syncthing(folder):
            self.st.remove_folder(fid)
        self._folders = [f for f in self._folders if f is not folder]
        self._save_folders()
        self._build_folder_grid()

    def _delete_physical_folder(self, folder):
        path = folder.get("path", "")
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "Delete Folder",
            f"Delete folder from disk?\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if shutil.which("gio"):
                subprocess.run(["gio", "trash", path], timeout=5)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            if folder.get("custom"):
                self._remove_custom_folder(folder, confirm=False)
            else:
                self._open_folder(folder)
        except Exception as e:
            log.warning("Delete folder failed %s: %s", path, e)

    def _pick_existing_dir(self, title, start_dir):
        if shutil.which("kdialog"):
            try:
                r = subprocess.run(
                    ["kdialog", "--getexistingdirectory", start_dir, "--title", title],
                    capture_output=True, text=True, timeout=30,
                )
                path = (r.stdout or "").strip()
                if r.returncode == 0 and path:
                    return path
            except Exception:
                pass
        return QFileDialog.getExistingDirectory(self, title, start_dir)

    def _pick_files(self, title, start_dir):
        if shutil.which("kdialog"):
            try:
                r = subprocess.run(
                    ["kdialog", "--getopenfilename", start_dir, "*", "--multiple", "--separate-output", "--title", title],
                    capture_output=True, text=True, timeout=60,
                )
                files = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
                if r.returncode == 0 and files:
                    return files
            except Exception:
                pass
        files, _ = QFileDialog.getOpenFileNames(self, title, start_dir, "All files (*)")
        return files

    def _thumbnail_label(self, full_path, ext, fallback_ico, allow_thumbnail=True):
        thumb = QLabel(fallback_ico)
        thumb.setFixedSize(36, 36)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background:rgba(255,255,255,0.07);border-radius:9px;font-size:18px;border:none;")

        if not allow_thumbnail:
            return thumb

        image_ext = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
        video_ext = {"mp4", "mov", "mkv", "webm", "avi", "3gp"}

        if ext in image_ext and os.path.exists(full_path):
            pix = QPixmap(full_path)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                           Qt.TransformationMode.SmoothTransformation))
                thumb.setText("")
            return thumb

        if ext in video_ext and os.path.exists(full_path):
            ffthumb = self._video_thumb_path(full_path)
            if ffthumb and os.path.exists(ffthumb):
                pix = QPixmap(ffthumb)
                if not pix.isNull():
                    thumb.setPixmap(pix.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                               Qt.TransformationMode.SmoothTransformation))
                    thumb.setText("")
            return thumb

        return thumb

    def _video_thumb_path(self, video_path):
        cache_dir = os.path.join(tempfile.gettempdir(), "phonebridge-thumbs")
        os.makedirs(cache_dir, exist_ok=True)
        key = hashlib.sha1(video_path.encode("utf-8")).hexdigest()
        out = os.path.join(cache_dir, f"{key}.jpg")
        if os.path.exists(out):
            return out
        if shutil.which("ffmpegthumbnailer"):
            try:
                subprocess.run(
                    ["ffmpegthumbnailer", "-i", video_path, "-o", out, "-s", "128", "-q", "8"],
                    capture_output=True,
                    timeout=6,
                )
                if os.path.exists(out):
                    return out
            except Exception:
                pass
        if shutil.which("ffmpeg"):
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-ss", "00:00:01", "-i", video_path,
                        "-frames:v", "1", "-vf", "scale=128:-1", out,
                    ],
                    capture_output=True,
                    timeout=8,
                )
                if os.path.exists(out):
                    return out
            except Exception:
                pass
        return None
