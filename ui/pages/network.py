"""Network page — Tailscale, KDE Connect, Syncthing, Bluetooth, Hotspot"""
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
)
from PyQt6.QtCore import QThread, pyqtSignal

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
from ui.motion import breathe
from backend.tailscale import Tailscale
from backend.syncthing import Syncthing
from backend.adb_bridge import ADBBridge
from backend.bluetooth_manager import BluetoothManager
from backend.kdeconnect import KDEConnect
from backend.ui_feedback import push_toast
from backend.state import state
import backend.settings_store as settings


class NetworkRefreshWorker(QThread):
    done = pyqtSignal(object)

    def __init__(self, target):
        super().__init__()
        self._target = target

    def run(self):
        ts = Tailscale()
        adb = ADBBridge(self._target)
        st = Syncthing()
        kc = KDEConnect()
        status = ts.get_status() or {}
        self_ip = ((status.get("Self", {}) or {}).get("TailscaleIPs", []) or [None])[0]
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
            "peers": peers,
            "tailscale": bool(self_ip),
            "kde": kde_enabled,
            "kde_reachable": kde_reachable,
            "syncthing": syncthing_ok,
            "wifi_enabled": wifi,
            "bt_enabled": bt,
            "connectivity_status": {
                "tailscale": {
                    "actual": bool(self_ip),
                    "reachable": bool(self_ip),
                    "reason": "mesh active" if self_ip else "not connected",
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
    done = pyqtSignal(bool, str)

    def __init__(self, action):
        super().__init__()
        self._action = action

    def run(self):
        ok, msg = self._action()
        self.done.emit(bool(ok), str(msg or ""))


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
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.12);
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
        self._bt_row = self._conn_toggle("⌬", "Bluetooth", "Paired fallback", True, VIOLET, self._toggle_bluetooth)
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
        t.blockSignals(True)
        t.setChecked(bool(checked))
        t.blockSignals(False)

    def _set_busy(self, row, busy):
        if not hasattr(row, "_toggle"):
            return
        row._toggle.setEnabled(not busy)

    def _run_toggle(self, row, action, fallback_label):
        if self._toggle_worker and self._toggle_worker.isRunning():
            return
        self._set_busy(row, True)
        self._toggle_worker = ToggleActionWorker(action)
        self._toggle_worker.done.connect(lambda ok, msg: self._finish_toggle(row, ok, msg, fallback_label))
        self._toggle_worker.finished.connect(self._toggle_worker.deleteLater)
        self._toggle_worker.start()

    def _finish_toggle(self, row, ok, msg, fallback_label):
        self._set_busy(row, False)
        if ok:
            push_toast(msg or "Updated", "success", 1500)
        else:
            push_toast(msg or fallback_label, "warning", 1900)
        self.refresh()

    def refresh(self):
        if self._refresh_busy:
            return
        self._refresh_busy = True
        self._refresh_worker = NetworkRefreshWorker(self.adb.target)
        self._refresh_worker.done.connect(self._apply_refresh)
        self._refresh_worker.finished.connect(self._refresh_worker.deleteLater)
        self._refresh_worker.start()

    def _apply_refresh(self, data):
        self._refresh_busy = False

        peers = (data or {}).get("peers", []) or []
        self_ip = (data or {}).get("self_ip")
        if self_ip:
            self._ts_sub.setText(f"{self_ip} · {len(peers)} peers · mesh active")
        else:
            self._ts_sub.setText("Not connected")

        self._set_toggle_state(self._ts_row, bool((data or {}).get("tailscale")))

        while self._peers_layout.count():
            item = self._peers_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for peer in peers:
            row = QWidget()
            row.setStyleSheet("background:rgba(255,255,255,0.03);border-radius:8px;border:none;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 8, 12, 8)
            rl.setSpacing(10)
            dot = QFrame()
            dot.setFixedSize(8, 8)
            dot_color = TEAL if peer["online"] else TEXT_DIM
            dot.setStyleSheet(f"background:{dot_color};border:none;border-radius:4px;")
            if peer["online"]:
                breathe(dot, min_opacity=0.35, max_opacity=1.0)
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

        bt_enabled = (data or {}).get("bt_enabled")
        if bt_enabled is not None:
            self._set_toggle_state(self._bt_row, bool(bt_enabled))

        state.set("connectivity_status", (data or {}).get("connectivity_status", {}))

    def _toggle_tailscale(self, checked):
        target = bool(checked)

        def _action():
            cmd_ok = self.ts.set_enabled(target)
            actual = bool(self.ts.is_connected())
            if actual != target:
                return False, "Tailscale did not reach requested state"
            return cmd_ok, "Tailscale connected" if target else "Tailscale disconnected"

        self._run_toggle(self._ts_row, _action, "Tailscale toggle failed")

    def _toggle_kde(self, checked):
        target = bool(checked)

        def _action():
            settings.set("kde_integration_enabled", target)
            actual = bool(settings.get("kde_integration_enabled", True))
            if actual != target:
                return False, "KDE toggle failed"
            return True, "KDE integration enabled" if target else "KDE integration disabled"

        self._run_toggle(self._kc_row, _action, "KDE toggle failed")

    def _toggle_syncthing(self, checked):
        target = bool(checked)

        def _action():
            ok = self.st.set_running(target)
            actual = bool(self.st.is_running())
            if actual != target:
                return False, "Syncthing service did not change state"
            return ok, "Syncthing running" if target else "Syncthing stopped"

        self._run_toggle(self._st_row, _action, "Syncthing toggle failed")

    def _toggle_hotspot(self, checked):
        target = bool(checked)

        def _action():
            ok = self.adb.set_hotspot(target)
            if not ok and target:
                self.adb.open_hotspot_settings()
                return False, "Hotspot command unavailable; opened phone settings"
            if not ok:
                return False, "Hotspot command failed"
            return True, "Hotspot enabled" if target else "Hotspot disabled"

        self._run_toggle(self._hs_row, _action, "Hotspot toggle failed")

    def _toggle_bluetooth(self, checked):
        target = bool(checked)

        def _action():
            ok = self.adb.set_bluetooth(target)
            actual = self.adb.get_bluetooth_enabled()
            if actual is None or bool(actual) != target:
                return False, "Bluetooth state not confirmed"
            if target and settings.get("auto_bt_connect", True):
                hints = [
                    settings.get("device_name", ""),
                    "nothing",
                    "phone",
                    "a059",
                ]
                self.bt.auto_connect_phone(hints)
            return ok, "Bluetooth enabled" if target else "Bluetooth disabled"

        self._run_toggle(self._bt_row, _action, "Bluetooth toggle failed")

    def _toggle_wifi(self, checked):
        target = bool(checked)

        def _action():
            ok = self.adb.set_wifi(target)
            actual = self.adb.get_wifi_enabled()
            if actual is None or bool(actual) != target:
                return False, "Wi-Fi state not confirmed"
            return ok, "Wi-Fi enabled" if target else "Wi-Fi disabled"

        self._run_toggle(self._wifi_row, _action, "Wi-Fi toggle failed")
