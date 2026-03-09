"""Action/test helper mixin for the Files page."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess

from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from ui.theme import BLUE
from ui.pages.files_backend import folder_syncthing_id, resolve_syncthing_folder

log = logging.getLogger(__name__)


class FilesActionsMixin:
    def _add_custom_folder(self):
        path = self._pick_existing_dir("Choose folder", os.path.expanduser("~"))
        if not path:
            return
        default_name = os.path.basename(path.rstrip("/")) or "Custom Folder"
        name = self._pick_text("Folder Name", "Display name:", default_name)
        if not name:
            return
        fid = f"custom-{hashlib.sha1(path.encode('utf-8')).hexdigest()[:10]}"
        self._folders.append(
            {
                "icon": "📁",
                "name": name,
                "id": fid,
                "path": path,
                "synced": False,
                "color": BLUE,
                "syncthing_id": "",
                "custom": True,
            }
        )
        self._save_folders()
        self.refresh()

    def add_custom_folder_for_test(self, folder_id: str, name: str, path: str) -> bool:
        folder_id = str(folder_id or "").strip()
        name = str(name or "").strip()
        path = str(path or "").strip()
        if not folder_id or not name or not path:
            return False
        existing = self._folder_by_id(folder_id)
        payload = {
            "icon": "🧪",
            "name": name,
            "id": folder_id,
            "path": path,
            "synced": False,
            "color": BLUE,
            "syncthing_id": "",
            "custom": True,
        }
        if existing is None:
            self._folders.append(payload)
        else:
            existing.update(payload)
        self._save_folders()
        log.info("Files page added test custom folder folder_id=%s path=%s", folder_id, path)
        self.refresh()
        return True

    def remove_custom_folder_for_test(self, folder_id: str) -> bool:
        folder_id = str(folder_id or "").strip()
        row = self._folder_by_id(folder_id)
        if row is None or not row.get("custom"):
            return False
        self._folders = [f for f in self._folders if str(f.get("id") or "") != folder_id]
        if self._current_folder and str(self._current_folder.get("id") or "") == folder_id:
            self._current_folder = None
        self._save_folders()
        log.info("Files page removed test custom folder folder_id=%s", folder_id)
        self.refresh()
        return True

    def open_folder_by_id(self, folder_id: str) -> bool:
        row = self._folder_by_id(folder_id)
        if row is None:
            return False
        self._open_folder(row)
        return True

    def create_subfolder_for_test(self, folder_id: str, name: str) -> bool:
        row = self._folder_by_id(folder_id)
        sub_name = str(name or "").strip()
        if row is None or not sub_name:
            return False
        target = os.path.join(str(row.get("path") or ""), sub_name)
        try:
            os.makedirs(target, exist_ok=True)
        except Exception:
            log.warning("Files page test subfolder creation failed path=%s", target, exc_info=True)
            return False
        log.info("Files page created test subfolder folder_id=%s path=%s", folder_id, target)
        self._open_folder(row)
        return True

    def _folder_syncthing_id(self, folder):
        return folder_syncthing_id(folder)

    def _is_synced_in_syncthing(self, folder):
        return resolve_syncthing_folder(self.st, folder) is not None

    def _refresh_sync_btn(self, folder, button):
        synced = bool(folder.get("synced", False))
        folder["synced"] = synced
        if synced:
            button.setText("Synced")
            button.setEnabled(False)
        else:
            button.setText("Sync Folder")
            button.setEnabled(True)

    def _toggle_syncthing_sync(self, folder, button):
        fid = self._folder_syncthing_id(folder)
        if not fid:
            return
        if bool(folder.get("synced", False)) or self._is_synced_in_syncthing(folder):
            self._refresh_sync_btn(folder, button)
            return
        button.setText("Working…")
        button.setEnabled(False)
        if self._start_mutation(
            mode="sync_add",
            folder=folder,
            callback=lambda payload, fid=fid, btn=button: self._on_sync_add_finished(fid, btn, payload),
        ):
            return
        self._refresh_sync_btn(folder, button)

    def _on_sync_add_finished(self, folder_id: str, button, payload):
        row = self._folder_by_id(folder_id)
        if row is None:
            return
        ok = bool((payload or {}).get("ok", False))
        if ok:
            self._update_folder_state(
                folder_id,
                syncthing_id=str((payload or {}).get("syncthing_id") or folder_id),
                synced=True,
            )
            self._save_folders()
        else:
            reason = str((payload or {}).get("reason") or "add_failed")
            if reason == "syncthing_inactive":
                QMessageBox.warning(self, "Syncthing", "Syncthing is not running on this system.")
            else:
                QMessageBox.warning(self, "Syncthing", "Failed to add folder to Syncthing.")
        self._refresh_sync_btn(row, button)

    def _pick_text(self, title, prompt, default_text):
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
        fid = str(folder.get("id") or "")
        if not fid:
            return
        if bool(folder.get("synced", False)) or self._is_synced_in_syncthing(folder):
            if self._start_mutation(
                mode="sync_remove",
                folder=folder,
                callback=lambda payload, folder_id=fid: self._on_sync_remove_and_drop(folder_id, payload),
            ):
                return
            return
        self._folders = [f for f in self._folders if str(f.get("id") or "") != fid]
        self._save_folders()
        self.refresh()

    def _on_sync_remove_and_drop(self, folder_id: str, payload):
        ok = bool((payload or {}).get("ok", False))
        reason = str((payload or {}).get("reason") or "")
        if not ok and reason not in {"not_synced"}:
            QMessageBox.warning(self, "Syncthing", "Failed to remove folder from Syncthing.")
            return
        self._folders = [f for f in self._folders if str(f.get("id") or "") != str(folder_id or "")]
        self._save_folders()
        if self._current_folder and str(self._current_folder.get("id") or "") == str(folder_id or ""):
            self._current_folder = None
        self.refresh()

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
                self.refresh()
        except Exception as exc:
            log.warning("Delete folder failed %s: %s", path, exc)

    def _pick_existing_dir(self, title, start_dir):
        return QFileDialog.getExistingDirectory(self, title, start_dir)

    def _pick_files(self, title, start_dir):
        files, _ = QFileDialog.getOpenFileNames(self, title, start_dir, "All files (*)")
        return files
