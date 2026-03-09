"""Background helpers for the Files page."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal

import backend.settings_store as settings
from backend.kdeconnect import KDEConnect
from backend.syncthing import Syncthing
from ui.theme import AMBER, BLUE, CYAN, ROSE, TEAL, VIOLET

log = logging.getLogger(__name__)

SYNC_ROOT = os.path.expanduser("~/PhoneSync")

DEFAULT_FOLDERS = [
    {"icon": "📲", "name": "KDE Connect Inbox", "id": "kdeconnect-inbox", "path": "~/Downloads", "synced": False, "color": "#6ea8ff"},
    {"icon": "📸", "name": "Camera Roll", "id": "phone-camera", "path": f"{SYNC_ROOT}/Camera", "synced": True, "color": TEAL},
    {"icon": "📄", "name": "Documents", "id": "phone-docs", "path": f"{SYNC_ROOT}/Documents", "synced": True, "color": CYAN},
    {"icon": "📥", "name": "Downloads", "id": "phone-downloads", "path": f"{SYNC_ROOT}/Downloads", "synced": False, "color": AMBER},
    {"icon": "💬", "name": "WhatsApp Media", "id": "phone-whatsapp", "path": f"{SYNC_ROOT}/WhatsApp", "synced": True, "color": "#25d366"},
    {"icon": "🖼️", "name": "Screenshots", "id": "phone-screenshots", "path": f"{SYNC_ROOT}/Screenshots", "synced": True, "color": VIOLET},
    {"icon": "📤", "name": "PhoneSend", "id": "phone-send", "path": f"{SYNC_ROOT}/PhoneSend", "synced": True, "color": ROSE},
    {"icon": "🎬", "name": "PhoneBridge Recordings", "id": "phonebridge-recordings", "path": f"{SYNC_ROOT}/PhoneBridgeRecordings", "synced": False, "color": BLUE},
]


def folder_syncthing_id(folder: dict) -> str:
    return (folder.get("syncthing_id") or folder.get("id") or "").strip()


def thumb_file_ready(path: str | None) -> bool:
    if not path or not os.path.exists(path):
        return False
    try:
        return os.path.getsize(path) > 0
    except Exception:
        return False


def video_thumb_path(video_path: str, *, cancel_check=None) -> str | None:
    cancel_check = cancel_check or (lambda: False)
    cache_dir = os.path.join(tempfile.gettempdir(), "phonebridge-thumbs")
    os.makedirs(cache_dir, exist_ok=True)
    try:
        st = os.stat(video_path)
        fingerprint = f"{video_path}|{int(st.st_mtime)}|{st.st_size}"
    except Exception:
        fingerprint = video_path
    key = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
    out = os.path.join(cache_dir, f"{key}.jpg")
    if thumb_file_ready(out):
        return out
    if os.path.exists(out):
        try:
            os.remove(out)
        except Exception:
            log.debug("Failed removing stale thumbnail %s", out, exc_info=True)
    if cancel_check():
        return None
    if shutil.which("ffmpegthumbnailer"):
        try:
            proc = subprocess.run(
                ["ffmpegthumbnailer", "-i", video_path, "-o", out, "-s", "128", "-q", "8"],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if thumb_file_ready(out):
                return out
            if proc.returncode != 0:
                log.debug("ffmpegthumbnailer failed for %s: %s", video_path, (proc.stderr or proc.stdout or "").strip())
        except Exception:
            log.debug("ffmpegthumbnailer exception for %s", video_path, exc_info=True)
    if cancel_check():
        return None
    if shutil.which("ffmpeg"):
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-ss", "00:00:01", "-i", video_path,
                    "-frames:v", "1", "-vf", "scale=128:-1", out,
                ],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if thumb_file_ready(out):
                return out
            proc2 = subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", video_path,
                    "-frames:v", "1", "-vf", "scale=128:-1", out,
                ],
                capture_output=True,
                text=True,
                timeout=8,
            )
            if thumb_file_ready(out):
                return out
            if proc.returncode != 0 or proc2.returncode != 0:
                log.debug(
                    "ffmpeg thumbnail failed for %s: first=%s second=%s",
                    video_path,
                    (proc.stderr or proc.stdout or "").strip(),
                    (proc2.stderr or proc2.stdout or "").strip(),
                )
        except Exception:
            log.debug("ffmpeg thumbnail exception for %s", video_path, exc_info=True)
    return None


def list_entries_payload(path: str, *, limit: int = 120, thumb_limit: int = 36, cancel_check=None):
    cancel_check = cancel_check or (lambda: False)
    rows = []
    truncated = False
    scan_cap = max(limit * 6, limit)
    try:
        with os.scandir(path) as it:
            for entry in it:
                if cancel_check():
                    return [], False
                try:
                    st = entry.stat(follow_symlinks=False)
                    ts = int(st.st_mtime)
                    size = int(st.st_size)
                except Exception:
                    ts = 0
                    size = 0
                rows.append(
                    {
                        "name": entry.name,
                        "full": entry.path,
                        "is_dir": entry.is_dir(follow_symlinks=False),
                        "ts": ts,
                        "size": size,
                    }
                )
                if len(rows) >= scan_cap:
                    truncated = True
                    break
    except Exception:
        log.debug("list_entries_payload failed path=%s", path, exc_info=True)
        return [], False
    rows.sort(key=lambda x: (-x.get("ts", 0), x["name"].lower()))
    if len(rows) > limit:
        rows = rows[:limit]
        truncated = True

    image_ext = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
    video_ext = {"mp4", "mov", "mkv", "webm", "avi", "3gp"}
    for idx, row in enumerate(rows):
        if cancel_check():
            return [], False
        if idx >= int(thumb_limit):
            row["thumb_path"] = None
            continue
        name = str(row.get("name") or "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        full_path = str(row.get("full") or "")
        if ext in image_ext and os.path.exists(full_path):
            row["thumb_path"] = full_path
        elif ext in video_ext and os.path.exists(full_path):
            row["thumb_path"] = video_thumb_path(full_path, cancel_check=cancel_check)
        else:
            row["thumb_path"] = None
    return rows, truncated


def load_folders_payload():
    overrides = settings.get("folder_overrides", {}) or {}
    custom = settings.get("custom_folders", []) or []
    folders = []
    kde_receive_path = KDEConnect().get_receive_path()
    st = Syncthing()
    syncthing_rows = st.get_folders() or []
    folder_by_id = {
        str(row.get("id") or "").strip(): row
        for row in syncthing_rows
        if str(row.get("id") or "").strip()
    }
    folder_paths = {
        os.path.normpath(str(row.get("path") or ""))
        for row in syncthing_rows
        if str(row.get("path") or "").strip()
    }
    for folder in DEFAULT_FOLDERS:
        merged = dict(folder)
        fid = merged.get("id")
        if fid == "kdeconnect-inbox":
            merged["path"] = kde_receive_path
        if fid in overrides and overrides[fid]:
            merged["path"] = overrides[fid]
        norm_path = os.path.normpath(str(merged.get("path") or ""))
        merged["synced"] = bool((fid and folder_by_id.get(str(fid))) or (norm_path and norm_path in folder_paths))
        folders.append(merged)
    for row in custom:
        if not isinstance(row, dict):
            continue
        name = (row.get("name") or "").strip()
        path = (row.get("path") or "").strip()
        if not name or not path:
            continue
        fid = (row.get("id") or f"custom-{hashlib.sha1(path.encode('utf-8')).hexdigest()[:10]}").strip()
        norm_path = os.path.normpath(path)
        folders.append(
            {
                "icon": row.get("icon") or "📁",
                "name": name,
                "id": fid,
                "path": path,
                "synced": bool((fid and folder_by_id.get(fid)) or (norm_path and norm_path in folder_paths)),
                "color": row.get("color") or BLUE,
                "syncthing_id": row.get("syncthing_id", ""),
                "custom": True,
            }
        )
    return folders


def resolve_syncthing_folder(st: Syncthing, folder: dict):
    fid = folder_syncthing_id(folder)
    path = os.path.normpath(str(folder.get("path", "") or ""))
    if fid:
        try:
            row = st.get_folder(fid)
        except Exception:
            row = None
        if row:
            return row
    try:
        rows = st.get_folders() or []
    except Exception:
        rows = []
    for row in rows:
        candidate = os.path.normpath(str((row or {}).get("path", "") or ""))
        if path and candidate and path == candidate:
            return row
    return None


class FilesLoadWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, *, token: int, mode: str, folder: dict | None = None, limit: int = 72):
        super().__init__()
        self._token = int(token)
        self._mode = str(mode or "grid")
        self._folder = dict(folder or {})
        self._limit = int(limit)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _is_cancelled(self):
        return bool(self._cancelled)

    def run(self):
        payload = {"token": self._token, "mode": self._mode}
        try:
            if self._mode == "grid":
                payload["folders"] = load_folders_payload()
            else:
                entries, truncated = list_entries_payload(
                    str(self._folder.get("path") or ""),
                    limit=self._limit,
                    cancel_check=self._is_cancelled,
                )
                payload["folder"] = dict(self._folder)
                payload["entries"] = entries
                payload["truncated"] = truncated
                payload["cancelled"] = self._is_cancelled()
        except Exception as exc:
            payload["error"] = str(exc)
        self.done.emit(payload)


class FilesMutationWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, *, mode: str, folder: dict | None = None):
        super().__init__()
        self._mode = str(mode or "")
        self._folder = dict(folder or {})

    def run(self):
        folder = dict(self._folder)
        payload = {
            "mode": self._mode,
            "folder": folder,
            "ok": False,
            "reason": "unknown",
            "synced": bool(folder.get("synced", False)),
            "syncthing_id": str(folder.get("syncthing_id") or ""),
        }
        try:
            st = Syncthing()
            row = resolve_syncthing_folder(st, folder)
            fid = str((row or {}).get("id") or folder_syncthing_id(folder) or "").strip()
            payload["syncthing_id"] = fid
            if self._mode == "sync_add":
                if not st.is_running():
                    payload["reason"] = "syncthing_inactive"
                elif not fid:
                    payload["reason"] = "missing_folder_id"
                elif row is not None:
                    payload["ok"] = True
                    payload["reason"] = "already_synced"
                    payload["synced"] = True
                else:
                    ok, created = st.add_folder(
                        path=folder.get("path", ""),
                        label=folder.get("name", ""),
                        folder_id=fid,
                        folder_type="sendreceive",
                    )
                    payload["ok"] = bool(ok)
                    payload["reason"] = "added" if ok else "add_failed"
                    payload["synced"] = bool(ok)
                    created_id = str(created or "").strip()
                    if created_id:
                        payload["syncthing_id"] = created_id
            elif self._mode == "sync_remove":
                if row is None or not fid:
                    payload["ok"] = True
                    payload["reason"] = "not_synced"
                    payload["synced"] = False
                    payload["syncthing_id"] = ""
                else:
                    payload["ok"] = bool(st.remove_folder(fid))
                    payload["reason"] = "removed" if payload["ok"] else "remove_failed"
                    payload["synced"] = False
                    payload["syncthing_id"] = ""
            else:
                payload["reason"] = "unsupported"
        except Exception as exc:
            payload["reason"] = str(exc) or "exception"
        self.done.emit(payload)
