"""Network page — Tailscale, KDE Connect, Syncthing, Bluetooth, Hotspot"""
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
)
from PyQt6.QtCore import QThread, pyqtSignal
import time

from ui.theme import (
    card_frame,
    lbl,
    section_label,
    toggle_switch,
    divider,
    TEAL,
    CYAN,
    VIOLET,
    TEXT_DIM,
)
from backend.tailscale import Tailscale
from backend.syncthing import Syncthing
from backend.adb_bridge import ADBBridge
from backend.bluetooth_manager import BluetoothManager
from backend.kdeconnect import KDEConnect
from backend.ui_feedback import push_toast
from backend.state import state
import backend.settings_store as settings
import backend.connectivity_controller as connectivity


class NetworkRefreshWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, target):
        super().__init__()
        self._target = target

    def run(self):
        payload = {
            "self_ip": None,
            "tailscale_state": "unknown",
            "tailscale_mesh_reason": "refresh unavailable",
            "peers": [],
            "tailscale": False,
            "tailscale_mesh_ready": False,
            "kde": bool(settings.get("kde_integration_enabled", True)),
            "kde_reachable": False,
            "syncthing": False,
            "syncthing_service_active": False,
            "syncthing_api_reachable": False,
            "syncthing_reason": "unknown",
            "wifi_enabled": None,
            "bt_enabled": None,
            "connectivity_status": {},
        }
        try:
            ts = Tailscale()
            if settings.get("tailscale_force_off", False) and ts.is_connected():
                ts.down()

            adb = ADBBridge(self._target)
            st = Syncthing()
            kc = KDEConnect()

            try:
                snapshot = ts.get_mesh_snapshot(
                    phone_name=settings.get("device_name", ""),
                    phone_ip=settings.get("phone_tailscale_ip", ""),
                )
            except Exception:
                snapshot = {}

            backend_state = str(snapshot.get("backend_state") or "").strip()
            local_connected = bool(snapshot.get("local_connected", False))
            mesh_ready = bool(snapshot.get("mesh_ready", False))
            mesh_reason = str(snapshot.get("mesh_reason") or "")
            self_ip = snapshot.get("self_ip") if local_connected else None
            peers = list(snapshot.get("peers", []) or [])

            kde_enabled = bool(settings.get("kde_integration_enabled", True))
            try:
                _raw = kc.is_reachable() if kde_enabled else None
                kde_reachable = _raw is True
                kde_status = (
                    "disabled" if not kde_enabled
                    else "reachable" if _raw is True
                    else "unreachable" if _raw is False
                    else "unknown"
                )
            except Exception:
                kde_reachable = False
                kde_status = "unknown"

            try:
                syncthing_status = st.get_runtime_status(timeout=3)
            except Exception:
                syncthing_status = {
                    "service_active": False,
                    "api_reachable": False,
                    "reason": "status_unavailable",
                    "unit_state": "unknown",
                    "unit_file_state": "unknown",
                }
            syncthing_service_active = bool(syncthing_status.get("service_active", False))
            syncthing_api_reachable = bool(syncthing_status.get("api_reachable", False))
            syncthing_reason = str(syncthing_status.get("reason") or "unknown")
            syncthing_unit_state = str(syncthing_status.get("unit_state") or "unknown")
            syncthing_unit_file_state = str(syncthing_status.get("unit_file_state") or "unknown")

            try:
                wifi = adb.get_wifi_enabled()
            except Exception:
                wifi = None
            try:
                bt = adb.get_bluetooth_enabled()
            except Exception:
                bt = None

            payload = {
                "self_ip": self_ip,
                "tailscale_state": backend_state or "unknown",
                "tailscale_mesh_reason": mesh_reason or "mesh unavailable",
                "peers": peers,
                "tailscale": bool(local_connected),
                "tailscale_mesh_ready": bool(mesh_ready),
                "kde": kde_enabled,
                "kde_reachable": kde_reachable,
                "kde_status": kde_status,
                "syncthing": syncthing_service_active,
                "syncthing_service_active": syncthing_service_active,
                "syncthing_api_reachable": syncthing_api_reachable,
                "syncthing_reason": syncthing_reason,
                "wifi_enabled": wifi,
                "bt_enabled": bt,
                "connectivity_status": {
                    "tailscale": {
                        "actual": bool(local_connected),
                        "reachable": bool(mesh_ready),
                        "reason": mesh_reason or f"state={backend_state or 'unknown'}",
                    },
                    "kde": {
                        "actual": kde_enabled,
                        "reachable": kde_reachable,
                        "reason": kde_status,
                    },
                    "syncthing": {
                        "actual": syncthing_service_active,
                        "reachable": syncthing_api_reachable,
                        "reason": f"{syncthing_reason} (unit={syncthing_unit_state}, file={syncthing_unit_file_state})",
                    },
                    "wifi": {
                        "actual": bool(wifi) if wifi is not None else False,
                        "reachable": wifi is not None,
                        "reason": "ok" if wifi is not None else "unknown",
                    },
                    "bluetooth": {
                        "actual": bool(bt) if bt is not None else False,
                        "reachable": bt is not None,
                        "reason": "ok" if bt is not None else "unknown",
                    },
                },
            }
        finally:
            self.done.emit(payload)


class ToggleActionWorker(QThread):
    done = pyqtSignal(bool, str, object)

    def __init__(self, action):
        super().__init__()
        self._action = action

    def run(self):
        try:
            out = self._action()
            if isinstance(out, tuple) and len(out) >= 3:
                ok, msg, actual = out[0], out[1], out[2]
            elif isinstance(out, tuple) and len(out) == 2:
                ok, msg = out
                actual = None
            else:
                ok, msg, actual = bool(out), "", None
        except Exception as e:
            ok, msg, actual = False, str(e), None
        self.done.emit(bool(ok), str(msg or ""), actual)


class NetworkPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        self.ts = Tailscale()
        self.st = Syncthing()
        self.adb = ADBBridge()
        self.bt = BluetoothManager()
        self._refresh_busy = False
        self._refresh_worker = None
        self._toggle_worker = None
        state.subscribe("connectivity_ops_busy", self._on_connectivity_ops_busy)
        self._build()
        self.refresh()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(lbl("Network & VPN", 22, bold=True))
        guide = card_frame()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(16, 12, 16, 12)
        gl.setSpacing(4)
        gl.addWidget(section_label("Flow"))
        gl.addWidget(
            lbl(
                "Use this page for all connectivity controls: mesh, KDE, Wi-Fi, Bluetooth, hotspot, and sync links.",
                11,
                TEXT_DIM,
            )
        )
        layout.addWidget(guide)

        ts_frame = QFrame()
        ts_frame.setStyleSheet(
            """
            QFrame {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 18px;
            }
        """
        )
        tl = QVBoxLayout(ts_frame)
        tl.setContentsMargins(20, 16, 20, 16)
        tl.setSpacing(12)

        ts_hdr = QHBoxLayout()
        ts_hdr.addWidget(lbl("◈", 20))
        ts_info = QVBoxLayout()
        ts_info.setSpacing(2)
        ts_info.addWidget(lbl("Tailscale Mesh", 14, bold=True))
        self._ts_sub = lbl("Checking…", 10, TEXT_DIM, mono=True)
        ts_info.addWidget(self._ts_sub)
        ts_hdr.addLayout(ts_info)
        ts_hdr.addStretch()
        self._ts_toggle = toggle_switch(True, TEAL)
        self._ts_toggle.toggled.connect(self._toggle_tailscale)
        self._ts_row = QWidget()
        self._ts_row._toggle = self._ts_toggle
        ts_hdr.addWidget(self._ts_toggle)
        tl.addLayout(ts_hdr)

        self._peers_layout = QVBoxLayout()
        self._peers_layout.setSpacing(5)
        tl.addLayout(self._peers_layout)
        layout.addWidget(ts_frame)

        phone_frame = card_frame()
        pl = QVBoxLayout(phone_frame)
        pl.setContentsMargins(0, 8, 0, 8)
        pl.setSpacing(0)

        self._kc_row = self._conn_toggle("⌁", "KDE Connect", "Signal bridge", True, TEAL, self._toggle_kde)
        self._wifi_row = self._conn_toggle("⌂", "Wi-Fi", "Phone Wi-Fi radio", True, CYAN, self._toggle_wifi)
        self._bt_row = self._conn_toggle("⌬", "Bluetooth", "Phone Bluetooth radio", True, VIOLET, self._toggle_bluetooth)
        self._st_row = self._conn_toggle("↺", "Syncthing", "Folder sync service", True, TEAL, self._toggle_syncthing)
        self._hs_row = self._conn_toggle("◉", "Hotspot", "Auto: USB tether (if wired) else Wi-Fi hotspot", False, CYAN, self._toggle_hotspot)

        for i, row in enumerate([self._kc_row, self._wifi_row, self._bt_row, self._st_row, self._hs_row]):
            if i > 0:
                pl.addWidget(divider())
            pl.addWidget(row)

        layout.addWidget(phone_frame)
        layout.addStretch()

    def _conn_toggle(self, ico, name, sub, on, color, on_toggle=None):
        w = QWidget()
        w.setStyleSheet("background:transparent;border:none;")
        row = QHBoxLayout(w)
        row.setContentsMargins(18, 11, 18, 11)
        row.setSpacing(12)
        row.addWidget(lbl(ico, 16))
        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(lbl(name, 13, bold=True))
        sub_lbl = lbl(sub, 11, TEXT_DIM)
        info.addWidget(sub_lbl)
        row.addLayout(info)
        row.addStretch()
        t = toggle_switch(on, color)
        if on_toggle:
            t.toggled.connect(lambda checked: on_toggle(checked))
        row.addWidget(t)
        w._toggle = t
        w._sub = sub_lbl
        return w

    def _set_toggle_state(self, row, checked):
        if not hasattr(row, "_toggle"):
            return
        t = row._toggle
        try:
            t.blockSignals(True)
            t.setChecked(bool(checked))
            t.blockSignals(False)
        except RuntimeError:
            pass

    def _set_busy(self, row, busy):
        if not hasattr(row, "_toggle"):
            return
        try:
            row._toggle.setEnabled(not busy)
        except RuntimeError:
            pass

    def _run_toggle(self, row, action, fallback_label):
        if self._toggle_worker is not None:
            try:
                if self._toggle_worker.isRunning():
                    return
            except RuntimeError:
                self._toggle_worker = None
        self._set_busy(row, True)
        worker = ToggleActionWorker(action)
        self._toggle_worker = worker
        worker.done.connect(lambda ok, msg, actual: self._finish_toggle(row, ok, msg, actual, fallback_label))
        worker.finished.connect(lambda: self._on_toggle_worker_finished(worker))
        worker.start()

    def _on_toggle_worker_finished(self, worker):
        if self._toggle_worker is worker:
            self._toggle_worker = None
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _finish_toggle(self, row, ok, msg, actual, fallback_label):
        self._set_busy(row, False)
        if actual is not None:
            self._set_toggle_state(row, bool(actual))
        if (not ok) and msg and "sudo tailscale set --operator=$USER" in msg:
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText("sudo tailscale set --operator=$USER")
                msg = f"{msg}\n(Command copied to clipboard)"
            except Exception:
                pass
        if ok:
            push_toast(msg or "Updated", "success", 1500)
        else:
            push_toast(msg or fallback_label, "warning", 1900)
        self.refresh()

    def _on_connectivity_ops_busy(self, payload):
        busy = payload or {}
        row_map = {
            "wifi": getattr(self, "_wifi_row", None),
            "bluetooth": getattr(self, "_bt_row", None),
            "tailscale": getattr(self, "_ts_row", None),
            "kde": getattr(self, "_kc_row", None),
            "syncthing": getattr(self, "_st_row", None),
        }
        for op, row in row_map.items():
            if row is None:
                continue
            self._set_busy(row, bool((busy or {}).get(op, False)))

    def refresh(self):
        if self._refresh_busy:
            return
        self._refresh_busy = True
        worker = NetworkRefreshWorker(self.adb.target)
        self._refresh_worker = worker
        worker.done.connect(self._apply_refresh)
        worker.finished.connect(lambda: self._on_refresh_worker_finished(worker))
        worker.start()

    def _on_refresh_worker_finished(self, worker):
        if self._refresh_worker is worker:
            self._refresh_worker = None
        self._refresh_busy = False
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _apply_refresh(self, data):
        self._refresh_busy = False

        peers = (data or {}).get("peers", []) or []
        self_ip = (data or {}).get("self_ip")
        ts_state = str((data or {}).get("tailscale_state") or "").strip()
        mesh_ready = bool((data or {}).get("tailscale_mesh_ready", False))
        mesh_reason = str((data or {}).get("tailscale_mesh_reason") or "").strip()
        online_count = sum(1 for p in peers if p.get("online"))
        total_count = len(peers)
        if self_ip:
            if mesh_ready:
                self._ts_sub.setText(f"{self_ip} · {online_count}/{total_count} devices online · mesh active")
            else:
                self._ts_sub.setText(
                    f"{self_ip} · {online_count}/{total_count} devices online · {mesh_reason or 'mesh degraded'}"
                )
        else:
            self._ts_sub.setText(f"Not connected ({ts_state or 'unknown'})")

        self._set_toggle_state(self._ts_row, bool((data or {}).get("tailscale")))

        while self._peers_layout.count():
            item = self._peers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for peer in peers:
            row = QWidget()
            row.setStyleSheet("background:rgba(255,255,255,0.05);border-radius:8px;border:none;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 8, 12, 8)
            rl.setSpacing(10)
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot_color = TEAL if peer["online"] else TEXT_DIM
            dot.setStyleSheet(f"background:{dot_color};border:none;border-radius:4px;")
            rl.addWidget(dot)
            name = str(peer.get("name") or "?")
            if peer.get("self"):
                name = f"{name} (this laptop)"
            rl.addWidget(lbl(name, 12, bold=True))
            rl.addStretch()
            rl.addWidget(lbl(str(peer.get("ip") or "?"), 10, TEXT_DIM, mono=True))
            self._peers_layout.addWidget(row)

        kde_on = bool((data or {}).get("kde", True))
        kde_reachable = bool((data or {}).get("kde_reachable", False))
        kde_status = str((data or {}).get("kde_status") or ("reachable" if kde_reachable else ("disabled" if not kde_on else "unreachable")))
        self._set_toggle_state(self._kc_row, kde_on)
        _kde_label = {
            "reachable": "Reachable",
            "unreachable": "Unreachable",
            "disabled": "Disabled",
            "unknown": "Unknown (D-Bus unavailable)",
        }.get(kde_status, "Unknown")
        self._kc_row._sub.setText(_kde_label)

        syncthing_service_active = bool((data or {}).get("syncthing_service_active", False))
        syncthing_api_reachable = bool((data or {}).get("syncthing_api_reachable", False))
        syncthing_reason = str((data or {}).get("syncthing_reason") or "unknown")
        self._set_toggle_state(self._st_row, syncthing_service_active)
        self._st_row._sub.setText(
            (
                "Service: active · API: reachable"
                if syncthing_service_active and syncthing_api_reachable
                else "Service: active · API: unreachable"
                if syncthing_service_active
                else "Service: inactive · API: reachable"
                if syncthing_api_reachable
                else f"Service: inactive · API: unreachable ({syncthing_reason})"
            )
        )

        wifi_enabled = (data or {}).get("wifi_enabled")
        if wifi_enabled is not None:
            self._set_toggle_state(self._wifi_row, bool(wifi_enabled))
            self._wifi_row._sub.setText("Phone Wi-Fi radio")
        else:
            self._set_toggle_state(self._wifi_row, False)
            self._wifi_row._sub.setText("Unknown (phone unreachable)")

        bt_enabled = (data or {}).get("bt_enabled")
        if bt_enabled is not None:
            self._set_toggle_state(self._bt_row, bool(bt_enabled))
            self._bt_row._sub.setText("Phone Bluetooth radio")
        else:
            self._set_toggle_state(self._bt_row, False)
            self._bt_row._sub.setText("Unknown (phone unreachable)")

        state.set("connectivity_status", (data or {}).get("connectivity_status", {}))

    @staticmethod
    def _wait_for_bool(getter, target, timeout_s=3.0, step_s=0.35):
        end = time.time() + timeout_s
        last = None
        while time.time() < end:
            last = getter()
            if last is not None and bool(last) == bool(target):
                return True
            time.sleep(step_s)
        return last is not None and bool(last) == bool(target)

    def _toggle_tailscale(self, checked):
        target = bool(checked)

        def _action():
            return connectivity.set_tailscale(target)

        self._run_toggle(self._ts_row, _action, "Tailscale toggle failed")

    def _toggle_kde(self, checked):
        target = bool(checked)
        def _action():
            return connectivity.set_kde(target, window=self.window())

        self._run_toggle(self._kc_row, _action, "KDE toggle failed")

    def _toggle_syncthing(self, checked):
        target = bool(checked)

        def _action():
            return connectivity.set_syncthing(target)

        self._run_toggle(self._st_row, _action, "Syncthing toggle failed")

    def _toggle_hotspot(self, checked):
        def _action():
            return self.adb.set_hotspot_smart(bool(checked))

        self._run_toggle(self._hs_row, _action, "Hotspot toggle failed")

    def _toggle_bluetooth(self, checked):
        target = bool(checked)

        def _action():
            return connectivity.set_bluetooth(target, target=self.adb.target)

        self._run_toggle(self._bt_row, _action, "Bluetooth toggle failed")

    def _toggle_wifi(self, checked):
        target = bool(checked)

        def _action():
            return connectivity.set_wifi(target, target=self.adb.target)

        self._run_toggle(self._wifi_row, _action, "Wi-Fi toggle failed")
