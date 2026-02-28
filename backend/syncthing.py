"""Syncthing REST API helpers."""
import os
import re
import subprocess
import httpx

URL = "http://127.0.0.1:8384"
API_KEY = "fCtXuD2RX3d52R7CMTfbzynGmNrHYFQ5"


class Syncthing:
    def __init__(self):
        self._h = {"X-API-Key": API_KEY}

    def _request(self, method, ep, params=None, json_body=None, timeout=5):
        try:
            r = httpx.request(
                method,
                f"{URL}{ep}",
                headers=self._h,
                params=params,
                json=json_body,
                timeout=timeout,
            )
            return r
        except Exception:
            return None

    def _get(self, ep, params=None, timeout=5):
        r = self._request("GET", ep, params=params, timeout=timeout)
        if not r or r.status_code != 200:
            return None
        try:
            return r.json()
        except Exception:
            return None

    def _put(self, ep, json_body=None, timeout=8):
        r = self._request("PUT", ep, json_body=json_body, timeout=timeout)
        return bool(r and r.status_code in (200, 204))

    def _delete(self, ep, timeout=8):
        r = self._request("DELETE", ep, timeout=timeout)
        return bool(r and r.status_code in (200, 204))

    def is_running(self):
        return self._get("/rest/system/ping") is not None

    def get_folders(self):
        folders_cfg = self._get("/rest/config/folders")
        if folders_cfg is None:
            cfg = self._get("/rest/config")
            folders_cfg = (cfg or {}).get("folders", [])
        out = []
        for f in folders_cfg or []:
            st = self._get("/rest/db/status", {"folder": f["id"]}) or {}
            out.append({
                "id": f.get("id", ""),
                "label": f.get("label") or f.get("id", ""),
                "path": f.get("path", ""),
                "state": st.get("state", "unknown"),
                "paused": bool(f.get("paused", False)),
                "globalBytes": st.get("globalBytes", 0),
                "inSyncBytes": st.get("inSyncBytes", 0),
                "needFiles": st.get("needFiles", 0),
            })
        return out

    def get_folder(self, folder_id):
        return self._get(f"/rest/config/folders/{folder_id}")

    def set_folder_paused(self, folder_id, paused):
        folder = self.get_folder(folder_id)
        if not folder:
            return False
        folder["paused"] = bool(paused)
        return self._put(f"/rest/config/folders/{folder_id}", json_body=folder)

    def update_folder_path(self, folder_id, new_path):
        folder = self.get_folder(folder_id)
        if not folder:
            return False
        path = str(new_path or "").strip()
        if not path:
            return False
        folder["path"] = path
        if not self._put(f"/rest/config/folders/{folder_id}", json_body=folder):
            return False
        verify = self.get_folder(folder_id) or {}
        return str(verify.get("path", "")).strip() == path

    def pause_folder(self, folder_id):
        return self.set_folder_paused(folder_id, True)

    def resume_folder(self, folder_id):
        return self.set_folder_paused(folder_id, False)

    def get_connections(self):
        return self._get("/rest/system/connections") or {}

    def get_transfer_rates(self):
        conns = self.get_connections()
        total = conns.get("total", {})
        return {
            "in_bps": total.get("inBytesTotal", 0),
            "out_bps": total.get("outBytesTotal", 0),
        }

    def get_devices(self):
        return self._get("/rest/config/devices") or []

    @staticmethod
    def make_folder_id(label, path):
        base = (label or "folder").strip().lower().replace(" ", "-")
        if not base:
            base = os.path.basename(path.rstrip("/")) or "folder"
        safe = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-._")
        return safe[:48] or "folder"

    def add_folder(self, path, label=None, folder_id=None, folder_type="sendreceive"):
        if not path:
            return False, "Missing path"
        os.makedirs(path, exist_ok=True)

        if not folder_id:
            folder_id = self.make_folder_id(label or "", path)

        existing = self.get_folder(folder_id)
        if existing:
            existing["path"] = path
            if label:
                existing["label"] = label
            existing["paused"] = False
            ok = self._put(f"/rest/config/folders/{folder_id}", json_body=existing)
            return ok, "updated" if ok else "update_failed"

        template = self._get("/rest/config/defaults/folder") or {
            "filesystemType": "basic",
            "rescanIntervalS": 3600,
            "fsWatcherEnabled": True,
            "type": "sendreceive",
            "copyOwnershipFromParent": False,
        }

        devices = self.get_devices()
        if devices:
            template["devices"] = [{
                "deviceID": d.get("deviceID", ""),
                "introducedBy": "",
                "encryptionPassword": "",
            } for d in devices if d.get("deviceID")]

        template["id"] = folder_id
        template["path"] = path
        template["label"] = label or os.path.basename(path.rstrip("/")) or folder_id
        template["type"] = folder_type
        template["paused"] = False

        ok = self._put(f"/rest/config/folders/{folder_id}", json_body=template)
        return ok, folder_id if ok else "create_failed"

    def remove_folder(self, folder_id):
        if not folder_id:
            return False
        return self._delete(f"/rest/config/folders/{folder_id}")

    def set_running(self, enabled: bool):
        cmd = [
            "systemctl",
            "--user",
            "start" if enabled else "stop",
            "syncthing.service",
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception:
            return False
        return bool(self.is_running() == bool(enabled))
