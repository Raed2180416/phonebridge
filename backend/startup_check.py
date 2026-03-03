"""Startup connectivity check popup and worker."""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Callable

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QPoint, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import backend.settings_store as settings
from backend.syncthing import Syncthing
from backend import preflight


PILL_STYLES = {
    "checking": "background: rgba(78,90,114,.12); border: 1px solid rgba(78,90,114,.2); color: #4e5a72;",
    "online": "background: rgba(34,197,94,.10); border: 1px solid rgba(34,197,94,.22); color: #22c55e;",
    "starting": "background: rgba(240,180,41,.10); border: 1px solid rgba(240,180,41,.22); color: #f0b429;",
    "offline": "background: rgba(240,82,82,.10); border: 1px solid rgba(240,82,82,.22); color: #f05252;",
    "error": "background: rgba(240,82,82,.10); border: 1px solid rgba(240,82,82,.22); color: #f05252;",
}


class StartupCheckWorker(QThread):
    """Runs connectivity checks concurrently and emits per-service results."""

    service_result = pyqtSignal(str, str, str)
    all_done = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._results: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()

    @property
    def results(self) -> dict[str, tuple[str, str]]:
        return dict(self._results)

    def run(self):
        checks: list[tuple[str, Callable[[], tuple[str, str]]]] = [
            ("tailscale", self._check_tailscale),
            ("kde", self._check_kde),
            ("syncthing", self._check_syncthing),
            ("bluetooth", self._check_bluetooth),
        ]

        threads: list[threading.Thread] = []
        for name, fn in checks:
            t = threading.Thread(target=self._run_check, args=(name, fn), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        self.all_done.emit()

    def _run_check(self, name: str, fn: Callable[[], tuple[str, str]]):
        try:
            status, detail = fn()
        except Exception:
            status, detail = "error", "Error"
        with self._lock:
            self._results[name] = (status, detail)
        self.service_result.emit(name, status, detail)

    def _check_tailscale(self) -> tuple[str, str]:
        try:
            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return "offline", "Daemon offline"
            data = json.loads(result.stdout or "{}")
            backend_state = str(data.get("BackendState") or "")
            if backend_state == "Starting":
                return "starting", "Starting"
            if backend_state == "Running":
                return "online", "Online"
            return "offline", backend_state or "Offline"
        except Exception:
            return "error", "Error"

    def _check_kde(self) -> tuple[str, str]:
        if not bool(settings.get("kde_integration_enabled", True)):
            return "offline", "Disabled"
        try:
            import dbus  # type: ignore

            bus = dbus.SessionBus()
            obj = bus.get_object("org.kde.kdeconnect", "/modules/kdeconnect")
            iface = dbus.Interface(obj, "org.kde.kdeconnect.daemon")
            devices = iface.devices(True, True)
            return ("online", "Reachable") if devices else ("offline", "No paired reachable device")
        except Exception:
            return "offline", "Offline"

    def _check_syncthing(self) -> tuple[str, str]:
        st = Syncthing()
        ok, status_code, reason = st.ping_status(timeout=3)
        if ok:
            return "online", "Online"
        if reason == "missing_api_key":
            return "error", "API key missing"
        if reason == "api_key_rejected":
            return "error", "API key rejected"
        if reason == "request_failed":
            return "offline", "Offline"
        if status_code is not None:
            return "starting", f"HTTP {status_code}"
        return "error", "Error"

    def _check_bluetooth(self) -> tuple[str, str]:
        try:
            result = subprocess.run(
                ["bluetoothctl", "show"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0:
                return "offline", "Offline"
            text = (result.stdout or "")
            return ("online", "Online") if "Powered: yes" in text else ("offline", "Disabled")
        except Exception:
            return "offline", "Offline"


class ServiceChip(QWidget):
    """One row in the startup connectivity popup."""

    def __init__(self, icon: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setStyleSheet(
            "QWidget { background:#1a1e28; border:1px solid #252b3b; border-radius:8px; }"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(11, 9, 11, 9)
        row.setSpacing(9)

        self.icon = QLabel(icon)
        self.icon.setFixedWidth(15)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet("color:#94a3b8;font-size:14px;border:none;background:transparent;")

        self.name = QLabel(label)
        self.name.setStyleSheet("color:#dde3f0;font-size:12px;font-weight:500;border:none;background:transparent;")

        self.pill = QLabel("Checking…")
        self.pill.setStyleSheet(
            "font-size:10px;font-family:monospace;padding:2px 8px;border-radius:9px;" + PILL_STYLES["checking"]
        )

        row.addWidget(self.icon)
        row.addWidget(self.name, 1)
        row.addWidget(self.pill)

    def set_status(self, status: str, detail: str = ""):
        key = status if status in PILL_STYLES else "error"
        label_map = {
            "checking": "Checking…",
            "online": "Online",
            "starting": "Starting…",
            "offline": "Offline",
            "error": "Error",
        }
        text = label_map.get(key, "Error")
        if detail and key in {"offline", "error", "starting"}:
            text = detail
        self.pill.setText(text)
        self.pill.setStyleSheet(
            "font-size:10px;font-family:monospace;padding:2px 8px;border-radius:9px;" + PILL_STYLES[key]
        )


class StartupCheckPopup(QWidget):
    """Reusable frameless popup showing startup connectivity status."""

    def __init__(self, main_window=None):
        super().__init__(None)
        self.main_window = main_window

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._worker: StartupCheckWorker | None = None
        self._user_interacted = False
        self._chip_anims: list[QPropertyAnimation] = []

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)

        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self._auto_close_if_idle)
        self._close_on_mouse_leave = False
        self._hover_close = QTimer(self)
        self._hover_close.setSingleShot(True)
        self._hover_close.timeout.connect(self.hide_popup)

        self._build()
        self.hide()

    def set_main_window(self, window):
        self.main_window = window

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = QFrame()
        card.setStyleSheet(
            """
            QFrame {
                background:#13161d;
                border:1px solid #252b3b;
                border-radius:12px;
            }
            """
        )
        root.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QVBoxLayout()
        header.setContentsMargins(15, 15, 15, 10)
        header.setSpacing(2)
        title = QLabel("Connectivity Check")
        title.setStyleSheet("color:#dde3f0;font-size:14px;font-weight:700;border:none;background:transparent;")
        sub = QLabel("PhoneBridge startup status")
        sub.setStyleSheet("color:#4e5a72;font-size:11px;border:none;background:transparent;")
        header.addWidget(title)
        header.addWidget(sub)
        layout.addLayout(header)

        chips_wrap = QVBoxLayout()
        chips_wrap.setContentsMargins(13, 0, 13, 13)
        chips_wrap.setSpacing(5)
        self.chips = {
            "tailscale": ServiceChip("🔒", "Tailscale"),
            "kde": ServiceChip("⛓", "KDE Connect"),
            "syncthing": ServiceChip("↺", "Syncthing"),
            "bluetooth": ServiceChip("⌬", "Bluetooth"),
        }
        for key in ("tailscale", "kde", "syncthing", "bluetooth"):
            chips_wrap.addWidget(self.chips[key])
        self._deps_lbl = QLabel("")
        self._deps_lbl.setWordWrap(True)
        self._deps_lbl.setStyleSheet(
            "color:#f0b429;font-size:10px;border:none;background:transparent;padding:2px 0;"
        )
        self._deps_lbl.setVisible(False)
        chips_wrap.addWidget(self._deps_lbl)
        layout.addLayout(chips_wrap)

        actions = QHBoxLayout()
        actions.setContentsMargins(13, 0, 13, 13)
        actions.setSpacing(7)

        self.open_btn = QPushButton("Open App")
        self.open_btn.setStyleSheet(
            """
            QPushButton {
                background:#4f8ef7;
                color:white;
                border:none;
                border-radius:8px;
                min-height:34px;
                font-size:12px;
                font-weight:600;
            }
            QPushButton:disabled {
                background:rgba(79,142,247,0.45);
                color:rgba(255,255,255,0.75);
            }
            """
        )
        self.open_btn.clicked.connect(self._open_app)

        self.dismiss_btn = QPushButton("Dismiss")
        self.dismiss_btn.setStyleSheet(
            """
            QPushButton {
                background:#1a1e28;
                color:#94a3b8;
                border:1px solid #252b3b;
                border-radius:8px;
                min-height:34px;
                font-size:12px;
                font-weight:500;
            }
            """
        )
        self.dismiss_btn.clicked.connect(self.hide_popup)

        actions.addWidget(self.open_btn, 1)
        actions.addWidget(self.dismiss_btn, 1)
        layout.addLayout(actions)

        self.setFixedWidth(350)

    def _position(self, mode: str, anchor_pos=None):
        from PyQt6.QtWidgets import QApplication

        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        if anchor_pos is not None:
            x = int(anchor_pos.x() - self.width() + 18)
            y = int(anchor_pos.y() - self.height() - 8)
            x = max(screen.x() + 8, min(x, screen.right() - self.width() - 8))
            y = max(screen.y() + 8, min(y, screen.bottom() - self.height() - 8))
            self.move(x, y)
            return
        if mode == "window" and self.main_window and self.main_window.isVisible():
            geo = self.main_window.geometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + max(20, (geo.height() - self.height()) // 3)
            self.move(x, y)
            return
        if mode == "tray":
            x = screen.right() - self.width() - 24
            y = screen.bottom() - self.height() - 24
            self.move(x, y)
            return
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() // 3
        self.move(x, y)

    def show_and_run(
        self,
        *,
        mode: str,
        auto_hide_ms: int | None = None,
        anchor_pos=None,
        close_on_mouse_leave: bool = False,
    ):
        self._user_interacted = False
        self._close_on_mouse_leave = bool(close_on_mouse_leave)
        self._hover_close.stop()
        for chip in self.chips.values():
            chip.set_status("checking")

        if self._worker is not None:
            try:
                self._worker.quit()
                self._worker.wait(100)
            except Exception:
                pass
            self._worker = None

        self._position(mode, anchor_pos=anchor_pos)

        self._opacity.setOpacity(0.0)
        self.show()
        self.raise_()
        self._animate_chip_intro()

        fade = QPropertyAnimation(self._opacity, b"opacity", self)
        fade.setDuration(200)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start()
        self._fade_in_anim = fade

        self._worker = StartupCheckWorker(self)
        self._worker.service_result.connect(self._on_service_result)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

        if auto_hide_ms and auto_hide_ms > 0:
            self._auto_hide.start(int(auto_hide_ms))
        else:
            self._auto_hide.stop()

    def enterEvent(self, event):
        self._hover_close.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._close_on_mouse_leave:
            self._hover_close.start(420)
        super().leaveEvent(event)

    def _animate_chip_intro(self):
        self._chip_anims.clear()
        order = ("tailscale", "kde", "syncthing", "bluetooth")
        for idx, key in enumerate(order):
            chip = self.chips.get(key)
            if chip is None:
                continue
            effect = chip.graphicsEffect()
            if not isinstance(effect, QGraphicsOpacityEffect):
                effect = QGraphicsOpacityEffect(chip)
                chip.setGraphicsEffect(effect)
            effect.setOpacity(0.0)

            def _start_anim(target_effect=effect):
                anim = QPropertyAnimation(target_effect, b"opacity", self)
                anim.setDuration(200)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                anim.start()
                self._chip_anims.append(anim)

            QTimer.singleShot(idx * 50, _start_anim)

    def _on_service_result(self, service: str, status: str, detail: str):
        chip = self.chips.get(str(service))
        if chip is not None:
            chip.set_status(status, detail)

    def _on_all_done(self):
        self.open_btn.setEnabled(True)
        self.open_btn.setText("Open App")
        missing = preflight.summary_lines()
        if missing and hasattr(self, "_deps_lbl"):
            self._deps_lbl.setText("⚠ Missing optional tools:\n" + "\n".join(missing[:4]))
            self._deps_lbl.setVisible(True)
            self.adjustSize()

    def _auto_close_if_idle(self):
        if not self._user_interacted:
            self.hide_popup()

    def _open_app(self):
        self._user_interacted = True
        if self.main_window is not None and hasattr(self.main_window, "show_and_raise"):
            self.main_window.show_and_raise()
        self.hide_popup()

    def hide_popup(self):
        self._user_interacted = True
        self._auto_hide.stop()
        self._hover_close.stop()
        if not self.isVisible():
            return
        fade = QPropertyAnimation(self._opacity, b"opacity", self)
        fade.setDuration(180)
        fade.setStartValue(self._opacity.opacity())
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)
        fade.finished.connect(self.hide)
        fade.start()
        self._fade_out_anim = fade


class StartupChecker:
    """Facade used by main/window to show or rerun startup checks."""

    _popup: StartupCheckPopup | None = None

    def __init__(self, main_window=None):
        self.main_window = main_window

    def _get_popup(self) -> StartupCheckPopup:
        if StartupChecker._popup is None:
            StartupChecker._popup = StartupCheckPopup(self.main_window)
        StartupChecker._popup.set_main_window(self.main_window)
        return StartupChecker._popup

    def run_and_show(
        self,
        *,
        from_tray: bool = False,
        background_mode: bool = False,
        anchor_pos=None,
        close_on_mouse_leave: bool = False,
    ):
        popup = self._get_popup()
        if from_tray:
            mode = "tray"
            auto_hide_ms = None
        elif self.main_window is not None and self.main_window.isVisible() and not background_mode:
            mode = "window"
            auto_hide_ms = None
        else:
            mode = "screen"
            auto_hide_ms = 10000 if background_mode else None
        popup.show_and_run(
            mode=mode,
            auto_hide_ms=auto_hide_ms,
            anchor_pos=anchor_pos,
            close_on_mouse_leave=close_on_mouse_leave,
        )
