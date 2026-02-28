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
        ts = Tailscale()
        if settings.get("tailscale_force_off", False) and ts.is_connected():
            ts.down()
        adb = ADBBridge(self._target)
        st = Syncthing()
        kc = KDEConnect()
        status = ts.get_status() or {}
        backend_state = str(status.get("BackendState") or "").strip()
        connected = backend_state == "Running"
        self_ip = ((status.get("Self", {}) or {}).get("TailscaleIPs", []) or [None])[0] if connected else None
        peers = [
            {
                "name": p.get("HostName", "?"),
                "ip": (p.get("TailscaleIPs") or ["?"])[0],
                "online": p.get("Online", False),
                "exit_node": p.get("ExitNode", False),
                "os": p.get("OS", ""),
                "relay": p.get("Relay", ""),
            }
            for p in (status.get("Peer", {}) or {}).values()
        ]
        kde_enabled = bool(settings.get("kde_integration_enabled", True))
        kde_reachable = bool(kde_enabled and kc.is_reachable())
        syncthing_ok = bool(st.is_running())
        wifi = adb.get_wifi_enabled()
        bt = adb.get_bluetooth_enabled()
        payload = {
            "self_ip": self_ip,
            "tailscale_state": backend_state,
            "peers": peers,
            "tailscale": bool(connected),
            "kde": kde_enabled,
            "kde_reachable": kde_reachable,
            "syncthing": syncthing_ok,
            "wifi_enabled": wifi,
            "bt_enabled": bt,
            "connectivity_status": {
                "tailscale": {
                    "actual": bool(connected),
                    "reachable": bool(connected),
                    "reason": f"state={backend_state or 'unknown'}",
                },
                "kde": {
                    "actual": kde_enabled,
                    "reachable": kde_reachable,
                    "reason": "reachable" if kde_reachable else ("disabled" if not kde_enabled else "unreachable"),
                },
                "syncthing": {
                    "actual": syncthing_ok,
                    "reachable": syncthing_ok,
                    "reason": "running" if syncthing_ok else "service stopped",
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
        self._hs_row = self._conn_toggle("◉", "Hotspot", "Open phone hotspot settings", False, CYAN, self._toggle_hotspot)

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
        if row is self._hs_row:
            self._set_toggle_state(self._hs_row, False)
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
        if self_ip:
            self._ts_sub.setText(f"{self_ip} · {len(peers)} peers · mesh active")
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
            rl.addWidget(lbl(peer["name"], 12, bold=True))
            rl.addStretch()
            rl.addWidget(lbl(peer["ip"], 10, TEXT_DIM, mono=True))
            self._peers_layout.addWidget(row)

        kde_on = bool((data or {}).get("kde", True))
        kde_reachable = bool((data or {}).get("kde_reachable", False))
        self._set_toggle_state(self._kc_row, kde_on)
        self._kc_row._sub.setText("Reachable" if kde_reachable else ("Disabled" if not kde_on else "Unreachable"))

        syncthing_on = bool((data or {}).get("syncthing", False))
        self._set_toggle_state(self._st_row, syncthing_on)
        self._st_row._sub.setText("Running" if syncthing_on else "Service stopped")

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
            ok = self.adb.open_hotspot_settings()
            if ok:
                return True, "Opened hotspot settings on phone"
            return False, "Could not open hotspot settings on phone"

        self._run_toggle(self._hs_row, _action, "Hotspot settings action failed")

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
