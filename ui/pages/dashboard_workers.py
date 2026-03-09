"""Background workers for the Dashboard page."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from backend.adb_bridge import ADBBridge
from backend.connectivity_snapshot import collect_snapshot
import backend.settings_store as settings


class DndWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, adb: ADBBridge, target_state=None):
        super().__init__()
        self._adb = adb
        self._target_state = target_state

    def run(self):
        if self._target_state is None:
            self.done.emit(self._adb.get_dnd_enabled())
            return
        self._adb.toggle_dnd(bool(self._target_state))
        self.done.emit(self._adb.get_dnd_enabled())


class DashboardRefreshWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, include_media=False, preferred_media_package: str = ""):
        super().__init__()
        self._include_media = include_media
        self._preferred_media_package = str(preferred_media_package or "")

    def run(self):
        try:
            result = collect_snapshot(
                include_media=self._include_media,
                preferred_media_package=self._preferred_media_package,
            )
        except Exception:
            result = {
                "battery": None,
                "network_type": None,
                "signal_strength": None,
                "media": None,
                "tailscale": False,
                "tailscale_local": False,
                "tailscale_ip": None,
                "tailscale_mesh_reason": "snapshot unavailable",
                "kde_enabled": bool(settings.get("kde_integration_enabled", True)),
                "kde_reachable": False,
                "kde_status": "unknown",
                "syncthing": False,
                "syncthing_service_active": False,
                "syncthing_api_reachable": False,
                "syncthing_reason": "status_unavailable",
                "syncthing_unit_file_state": "unknown",
                "wifi_enabled": None,
                "bt_enabled": None,
            }
        self.done.emit(result)
