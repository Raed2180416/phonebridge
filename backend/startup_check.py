"""Startup connectivity checker."""
import json
import subprocess

import httpx
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
)
from PyQt6.QtCore import Qt, QTimer

import backend.settings_store as settings

API_KEY = "fCtXuD2RX3d52R7CMTfbzynGmNrHYFQ5"


class ConnectivityChecker:
    def check_tailscale(self):
        try:
            r = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if r.returncode != 0:
                return {"ok": False, "detail": "tailscale daemon offline"}
            payload = json.loads(r.stdout or "{}")
            backend_state = payload.get("BackendState")
            ip = ((payload.get("Self") or {}).get("TailscaleIPs") or [None])[0]
            peers = len((payload.get("Peer") or {}).values())
            ok = backend_state == "Running" and bool(ip)
            detail = f"{ip} · {peers} peers" if ok else f"state: {backend_state or 'unknown'}"
            return {"ok": ok, "detail": detail}
        except Exception as e:
            return {"ok": False, "detail": f"error: {type(e).__name__}"}

    def check_kde_connect(self):
        try:
            import dbus

            bus = dbus.SessionBus()
            obj = bus.get_object("org.kde.kdeconnect", "/modules/kdeconnect")
            iface = dbus.Interface(obj, "org.kde.kdeconnect.daemon")
            devices = iface.devices(True, True)
            ok = len(devices) > 0 and bool(settings.get("kde_integration_enabled", True))
            detail = f"{len(devices)} reachable device(s)" if ok else "no reachable paired device"
            if not settings.get("kde_integration_enabled", True):
                detail = "disabled in app settings"
            return {"ok": ok, "detail": detail}
        except Exception as e:
            return {"ok": False, "detail": f"error: {type(e).__name__}"}

    def check_syncthing(self):
        try:
            r = httpx.get(
                "http://127.0.0.1:8384/rest/system/ping",
                headers={"X-API-Key": API_KEY},
                timeout=3,
            )
            ok = r.status_code == 200
            detail = "REST API reachable" if ok else f"HTTP {r.status_code}"
            return {"ok": ok, "detail": detail}
        except Exception as e:
            return {"ok": False, "detail": f"error: {type(e).__name__}"}

    def check_adb(self):
        target = settings.get("adb_target", "100.127.0.90:5555")
        try:
            r = subprocess.run(
                ["adb", "-s", target, "get-state"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            ok = r.returncode == 0 and "device" in (r.stdout or "")
            detail = f"{target} reachable" if ok else f"{target} not reachable"
            return {"ok": ok, "detail": detail}
        except Exception as e:
            return {"ok": False, "detail": f"error: {type(e).__name__}"}

    def run_all(self):
        tailscale = self.check_tailscale()
        kde = self.check_kde_connect()
        syncthing = self.check_syncthing()
        adb = self.check_adb()

        checks = [
            {"icon": "🔗", "label": "Tailscale Mesh", **tailscale},
            {"icon": "📱", "label": "KDE Connect", **kde},
            {"icon": "↺", "label": "Syncthing", **syncthing},
            {"icon": "⌁", "label": "ADB Link", **adb},
        ]
        all_ok = all(c.get("ok") for c in checks[:3])
        return {
            "checks": checks,
            "all_ok": all_ok,
        }


class StartupPopout(QDialog):
    def __init__(self, results, main_window=None):
        super().__init__()
        self.main_window = main_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._build(results)
        self._position()

    def _build(self, payload):
        checks = list((payload or {}).get("checks", []) or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                background: rgba(7,12,23,246);
                border: 1px solid rgba(62,240,176,0.28);
                border-radius: 16px;
            }
        """
        )
        frame.setMinimumWidth(360)
        fl = QVBoxLayout(frame)
        fl.setSpacing(10)
        fl.setContentsMargins(18, 14, 18, 14)

        hrow = QHBoxLayout()
        title = QLabel("⌘  PhoneBridge")
        title.setStyleSheet("color:white;font-size:14px;font-weight:600;background:transparent;border:none;")
        close = QPushButton("✕")
        close.setFixedSize(20, 20)
        close.setStyleSheet("background:transparent;color:rgba(255,255,255,0.35);border:none;font-size:13px;")
        close.clicked.connect(self.close)
        hrow.addWidget(title)
        hrow.addStretch()
        hrow.addWidget(close)
        fl.addLayout(hrow)

        sub = QLabel("Startup connectivity checks")
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.35);font-size:10px;font-family:monospace;background:transparent;border:none;"
        )
        fl.addWidget(sub)

        for check in checks:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(self._lbl(check.get("icon", "•"), 14))
            info = QVBoxLayout()
            info.setSpacing(1)
            info.addWidget(self._lbl(check.get("label", "Service"), 12, "rgba(255,255,255,0.85)"))
            info.addWidget(self._lbl(check.get("detail", ""), 10, "rgba(255,255,255,0.45)", mono=True))
            row.addLayout(info)
            row.addStretch()

            ok = bool(check.get("ok"))
            chip_color = "#3ef0b0" if ok else "#f472b6"
            chip = QLabel("● online" if ok else "● offline")
            chip.setStyleSheet(
                f"""
                color:{chip_color};
                background:{chip_color}18;
                border:1px solid {chip_color}55;
                border-radius:9px;
                padding:2px 8px;
                font-size:10px;
                font-family:monospace;
            """
            )
            row.addWidget(chip)
            fl.addLayout(row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:rgba(255,255,255,0.07);background:rgba(255,255,255,0.07);max-height:1px;")
        fl.addWidget(sep)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        for txt, style, hover, cb in [
            ("Dismiss", "rgba(255,255,255,0.05)", "rgba(255,255,255,0.1)", self.close),
            ("Open App", "rgba(62,240,176,0.12)", "rgba(62,240,176,0.22)", self._open),
        ]:
            btn = QPushButton(txt)
            is_open = txt == "Open App"
            color = "#3ef0b0" if is_open else "rgba(255,255,255,0.6)"
            border = "rgba(62,240,176,0.30)" if is_open else "rgba(255,255,255,0.12)"
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background:{style};border:1px solid {border};
                    border-radius:8px;color:{color};
                    padding:7px 14px;font-size:12px;
                }}
                QPushButton:hover {{ background:{hover}; }}
            """
            )
            btn.clicked.connect(cb)
            brow.addWidget(btn)
        fl.addLayout(brow)

        layout.addWidget(frame)

    def _lbl(self, text, size=12, color="rgba(255,255,255,0.88)", bold=False, mono=False):
        l = QLabel(text)
        l.setStyleSheet(
            f"""
            color:{color};font-size:{size}px;
            font-weight:{'600' if bold else '400'};
            font-family:{'monospace' if mono else 'sans-serif'};
            background:transparent;border:none;
        """
        )
        return l

    def _open(self):
        if self.main_window:
            self.main_window.show_and_raise()
        self.close()

    def _position(self):
        from PyQt6.QtWidgets import QApplication

        self.adjustSize()
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 24, geo.bottom() - self.height() - 24)


class StartupChecker:
    def __init__(self, main_window=None):
        self.main_window = main_window

    def run_and_show(self):
        results = ConnectivityChecker().run_all()
        popout = StartupPopout(results, self.main_window)
        popout.show()
        if results.get("all_ok"):
            QTimer.singleShot(8000, popout.close)
