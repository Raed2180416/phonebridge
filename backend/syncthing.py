"""Syncthing REST API helpers."""
import os
import re
import subprocess
import shutil
import time
import httpx
import backend.settings_store as settings


def resolve_syncthing_config():
    url = str(settings.get("syncthing_url", "http://127.0.0.1:8384") or "").strip()
    key = str(settings.get("syncthing_api_key", "") or "").strip()
    if not url:
        url = "http://127.0.0.1:8384"
    return url.rstrip("/"), key


class Syncthing:
    def __init__(self):
        self._url, self._api_key = resolve_syncthing_config()
        self._h = {"X-API-Key": self._api_key} if self._api_key else {}

    def _request(self, method, ep, params=None, json_body=None, timeout=5):
        try:
            r = httpx.request(
                method,
                f"{self._url}{ep}",
                headers=self._h,
                params=params,
                json=json_body,
                timeout=timeout,
            )
            return r
        except Exception:
            return None

    def ping_status(self, timeout=3):
        if not self._api_key:
            return False, None, "missing_api_key"
        r = self._request("GET", "/rest/system/ping", timeout=timeout)
        if not r:
            return False, None, "request_failed"
        if r.status_code == 200:
            return True, 200, "ok"
        if r.status_code in {401, 403}:
            return False, r.status_code, "api_key_rejected"
        return False, r.status_code, "http_error"

    def service_state(self):
        if not shutil.which("systemctl"):
            return {
                "service_active": False,
                "unit_state": "unknown",
                "unit_file_state": "unknown",
                "load_state": "unknown",
                "detail": "systemctl_unavailable",
            }

        unit_state = "unknown"
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "is-active", "syncthing.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            unit_state = str((proc.stdout or proc.stderr or "").strip() or "unknown")
        except Exception:
            unit_state = "unknown"

        show = {}
        try:
            proc = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    "syncthing.service",
                    "--property=UnitFileState",
                    "--property=LoadState",
                    "--property=ActiveState",
                ],
                capture_output=True,
                text=True,
                timeout=6,
            )
            for line in (proc.stdout or "").splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                show[k.strip()] = v.strip()
        except Exception:
            show = {}

        active_state = str(show.get("ActiveState") or "").strip()
        if active_state:
            unit_state = active_state
        unit_file_state = str(show.get("UnitFileState") or "unknown").strip() or "unknown"
        load_state = str(show.get("LoadState") or "unknown").strip() or "unknown"
        service_active = unit_state == "active"

        detail = unit_state
        if unit_file_state == "linked-runtime":
            detail = "linked_runtime"
        elif unit_state in {"inactive", "failed"}:
            detail = f"unit_{unit_state}"

        return {
            "service_active": bool(service_active),
            "unit_state": unit_state,
            "unit_file_state": unit_file_state,
            "load_state": load_state,
            "detail": detail,
        }

    def get_runtime_status(self, timeout=3):
        svc = self.service_state()
        api_ok, api_code, api_reason = self.ping_status(timeout=timeout)
        service_active = bool(svc.get("service_active", False))
        api_reachable = bool(api_ok)

        if service_active and api_reachable:
            reason = "running"
        elif service_active and not api_reachable:
            reason = f"service_active_{api_reason}"
        elif (not service_active) and api_reachable:
            reason = "unit_inactive_api_reachable"
        else:
            reason = str(svc.get("detail") or "service_inactive")

        return {
            "service_active": service_active,
            "api_reachable": api_reachable,
            "unit_state": str(svc.get("unit_state") or "unknown"),
            "unit_file_state": str(svc.get("unit_file_state") or "unknown"),
            "load_state": str(svc.get("load_state") or "unknown"),
            "api_status_code": api_code,
            "api_reason": str(api_reason),
            "mixed_state": bool(service_active != api_reachable),
            "reason": reason,
        }

    def is_service_active(self):
        return bool(self.service_state().get("service_active", False))

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
        # Keep backward compatibility: "running" now maps to service state,
        # not API reachability.
        return self.is_service_active()

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
        deadline = time.time() + 5.0
        desired = bool(enabled)
        last = self.is_service_active()
        while time.time() < deadline:
            last = self.is_service_active()
            if bool(last) == desired:
                return True
            time.sleep(0.35)
        return bool(last) == desired
