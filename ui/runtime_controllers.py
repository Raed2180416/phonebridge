"""Lightweight runtime controllers for window-owned timers and startup hooks."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import QApplication

import backend.settings_store as settings
from backend.clipboard_history import sanitize_clipboard_history
from backend.state import state

log = logging.getLogger(__name__)


class ConnectivityController(QObject):
    """Owns the periodic mobile-data policy timer."""

    def __init__(self, parent: QObject, tick_callback):
        super().__init__(parent)
        self._tick_callback = tick_callback
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._safe_tick)

    def start(self, interval_ms: int = 15_000, *, immediate: bool = True) -> None:
        self._timer.start(max(250, int(interval_ms)))
        if immediate:
            self._safe_tick()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def _safe_tick(self) -> None:
        try:
            self._tick_callback()
        except Exception:
            log.exception("ConnectivityController tick failed")


class HealthController(QObject):
    """Owns periodic KDE + service health probes."""

    def __init__(self, parent: QObject, kde_tick, service_tick):
        super().__init__(parent)
        self._kde_tick = kde_tick
        self._service_tick = service_tick
        self._kde_timer = QTimer(self)
        self._kde_timer.timeout.connect(self._safe_kde_tick)
        self._service_timer = QTimer(self)
        self._service_timer.timeout.connect(self._safe_service_tick)

    def start(self) -> None:
        self.resume()
        QTimer.singleShot(3_000, self._safe_kde_tick)
        QTimer.singleShot(8_000, self._safe_service_tick)

    def resume(self) -> None:
        if not self._kde_timer.isActive():
            self._kde_timer.start(30_000)
        if not self._service_timer.isActive():
            self._service_timer.start(60_000)

    def suspend(self) -> None:
        if self._kde_timer.isActive():
            self._kde_timer.stop()
        if self._service_timer.isActive():
            self._service_timer.stop()

    def stop(self) -> None:
        self.suspend()

    def _safe_kde_tick(self) -> None:
        try:
            self._kde_tick()
        except Exception:
            log.exception("HealthController KDE tick failed")

    def _safe_service_tick(self) -> None:
        try:
            self._service_tick()
        except Exception:
            log.exception("HealthController service tick failed")


class NotificationController(QObject):
    """Owns startup-only notification mirror/policy scheduling."""

    def __init__(self, parent: QObject, sync_snapshot, enforce_popup_policy):
        super().__init__(parent)
        self._sync_snapshot = sync_snapshot
        self._enforce_popup_policy = enforce_popup_policy

    def prime_startup(self) -> None:
        QTimer.singleShot(900, self._safe_sync_snapshot)
        QTimer.singleShot(1_200, self._safe_enforce_popup_policy)

    def sync_snapshot_now(self) -> None:
        self._safe_sync_snapshot()

    def enforce_popup_policy_now(self) -> None:
        self._safe_enforce_popup_policy()

    def _safe_sync_snapshot(self) -> None:
        try:
            self._sync_snapshot()
        except Exception:
            log.exception("NotificationController snapshot sync failed")

    def _safe_enforce_popup_policy(self) -> None:
        try:
            self._enforce_popup_policy()
        except Exception:
            log.exception("NotificationController popup policy enforcement failed")


class ClipboardController(QObject):
    """Owns clipboard sync, history persistence, and Wayland safety polling."""

    _wayland_text_ready = pyqtSignal(str)

    def __init__(self, parent: QObject):
        super().__init__(parent)
        self._connected = False
        self._last_clipboard_text = ""
        self._clipboard_history = sanitize_clipboard_history(settings.get("clipboard_history", []) or [])
        self._wayland_clipboard_cache = ""
        self._wayland_poll_busy = False
        self._wayland_poll_pending = False
        settings.set("clipboard_history", self._clipboard_history)
        state.set("clipboard_history", self._clipboard_history)
        self._wayland_text_ready.connect(self._on_wayland_text_ready)

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._on_local_clipboard_changed)

    def start(self, interval_ms: int = 1_200) -> None:
        clip = QApplication.clipboard()
        if not self._connected:
            clip.dataChanged.connect(self._on_local_clipboard_changed)
            clip.selectionChanged.connect(self._on_local_clipboard_changed)
            self._connected = True
        self._on_local_clipboard_changed()
        self._poll_timer.start(max(250, int(interval_ms)))

    def stop(self) -> None:
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._connected:
            clip = QApplication.clipboard()
            try:
                clip.dataChanged.disconnect(self._on_local_clipboard_changed)
            except Exception:
                log.debug("ClipboardController failed disconnecting dataChanged", exc_info=True)
            try:
                clip.selectionChanged.disconnect(self._on_local_clipboard_changed)
            except Exception:
                log.debug("ClipboardController failed disconnecting selectionChanged", exc_info=True)
            self._connected = False

    def apply_remote_text(self, text: str) -> None:
        value = str(text or "")
        log.info("ClipboardController remote text length=%s", len(value))
        if settings.get("clipboard_autoshare", True):
            QApplication.clipboard().setText(value)
        self._push_history(value, source="phone")
        state.set("clipboard_text", value)
        self._last_clipboard_text = value

    def _on_local_clipboard_changed(self) -> None:
        text = self._read_current_text()
        if text == self._last_clipboard_text:
            return
        self._last_clipboard_text = text
        if text:
            state.set("clipboard_text", text)
            self._push_history(text, source="pc")

    def _read_current_text(self) -> str:
        text = ""
        clip = QApplication.clipboard()
        try:
            text = clip.text(QClipboard.Mode.Clipboard) or ""
        except Exception:
            text = ""
        if not text:
            try:
                text = clip.text(QClipboard.Mode.Selection) or ""
            except Exception:
                text = ""
        if not text:
            self._schedule_wayland_clipboard_refresh()
            text = self._read_wayland_clipboard_text()
        return str(text or "")

    def _read_wayland_clipboard_text(self) -> str:
        return str(self._wayland_clipboard_cache or "")

    def _schedule_wayland_clipboard_refresh(self) -> None:
        if not os.environ.get("WAYLAND_DISPLAY"):
            return
        if self._wayland_poll_busy:
            self._wayland_poll_pending = True
            return
        self._wayland_poll_busy = True

        def _job():
            self._wayland_text_ready.emit(self._poll_wayland_clipboard_text())

        threading.Thread(target=_job, daemon=True, name="pb-wayland-clipboard").start()

    @staticmethod
    def _poll_wayland_clipboard_text() -> str:
        if not os.environ.get("WAYLAND_DISPLAY"):
            return ""
        for args in (["wl-paste", "--no-newline"], ["wl-paste", "--primary", "--no-newline"]):
            try:
                cp = subprocess.run(
                    args,
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                    check=False,
                )
                if cp.returncode == 0:
                    return (cp.stdout or "").strip()
            except Exception:
                log.debug("ClipboardController wl-paste failed args=%s", args, exc_info=True)
        return ""

    def _on_wayland_text_ready(self, text: str) -> None:
        value = str(text or "")
        self._wayland_clipboard_cache = value
        self._wayland_poll_busy = False
        rerun = self._wayland_poll_pending
        self._wayland_poll_pending = False
        if value and value != self._last_clipboard_text:
            self._last_clipboard_text = value
            state.set("clipboard_text", value)
            self._push_history(value, source="pc")
        if rerun:
            self._schedule_wayland_clipboard_refresh()

    def _push_history(self, text: str, *, source: str) -> None:
        value = str(text or "").strip()
        if not value:
            return
        last = self._clipboard_history[-1] if self._clipboard_history else {}
        if str(last.get("text") or "") == value:
            return
        self._clipboard_history.append(
            {
                "text": value,
                "ts": int(time.time()),
                "source": str(source or "phone"),
            }
        )
        self._clipboard_history = sanitize_clipboard_history(self._clipboard_history, limit=200)
        settings.set("clipboard_history", self._clipboard_history)
        state.set("clipboard_history", self._clipboard_history)


class CallController(QObject):
    """Adaptive fallback call-state polling controller."""

    # This poller is a fallback for imperfect KDE call signals, not the primary
    # source of truth. Keep idle pressure low so flaky wireless ADB does not
    # saturate the app during normal background usage.
    ACTIVE_INTERVAL_MS = 700
    DEGRADED_INTERVAL_MS = 1_000
    IDLE_VISIBLE_INTERVAL_MS = 1_000
    IDLE_HIDDEN_INTERVAL_MS = 1_500

    def __init__(self, parent: QObject, poll_callback):
        super().__init__(parent)
        self._poll_callback = poll_callback
        self._window_visible = True
        self._mode = "idle"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._safe_poll)

    def start(self, *, visible: bool) -> None:
        self._window_visible = bool(visible)
        self._apply_mode(self._idle_mode())
        QTimer.singleShot(200, self._safe_poll)

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def set_window_visible(self, visible: bool) -> None:
        self._window_visible = bool(visible)
        if self._mode in {"idle_visible", "idle_hidden", "idle"}:
            self._apply_mode(self._idle_mode())

    def note_signal_event(self, normalized_event: str) -> None:
        event = str(normalized_event or "").strip().lower()
        if event in {"ringing", "talking"}:
            self._apply_mode("active")
            return
        if event in {"ended", "missed_call", "idle"}:
            self._apply_mode(self._idle_mode())

    def note_polled_state(self, polled_state: str) -> None:
        value = str(polled_state or "").strip().lower()
        if value in {"ringing", "offhook"}:
            self._apply_mode("active")
        elif value == "unknown":
            self._apply_mode("degraded")
        else:
            self._apply_mode(self._idle_mode())

    def mode(self) -> str:
        return self._mode

    def interval_ms(self) -> int:
        return int(self._timer.interval() or 0)

    def _idle_mode(self) -> str:
        return "idle_visible" if self._window_visible else "idle_hidden"

    def _apply_mode(self, mode: str) -> None:
        requested = str(mode or "idle_visible")
        interval = {
            "active": self.ACTIVE_INTERVAL_MS,
            "degraded": self.DEGRADED_INTERVAL_MS,
            "idle_visible": self.IDLE_VISIBLE_INTERVAL_MS,
            "idle_hidden": self.IDLE_HIDDEN_INTERVAL_MS,
        }.get(requested, self.IDLE_VISIBLE_INTERVAL_MS)
        self._mode = requested
        self._timer.start(interval)

    def _safe_poll(self) -> None:
        try:
            self._poll_callback()
        except Exception:
            log.exception("CallController poll callback failed")
