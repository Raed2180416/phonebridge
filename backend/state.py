"""Simple in-memory app state and pub/sub hooks."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

try:
    from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
    from PyQt6.QtWidgets import QApplication
except Exception:  # pragma: no cover - Qt may be unavailable in non-UI contexts
    QObject = None
    QThread = None
    Qt = None
    pyqtSignal = None
    QApplication = None


if QObject is not None and pyqtSignal is not None and Qt is not None:
    class _QtDispatchBridge(QObject):
        _deliver = pyqtSignal(object, object)

        def __init__(self):
            super().__init__()
            self._deliver.connect(self._run, Qt.ConnectionType.QueuedConnection)

        def emit(self, callback: Callable[[Any], None], value: Any) -> None:
            self._deliver.emit(callback, value)

        @staticmethod
        def _run(callback: Callable[[Any], None], value: Any) -> None:
            try:
                callback(value)
            except Exception:
                pass


    _QT_BRIDGE = _QtDispatchBridge()
else:
    _QT_BRIDGE = None


class AppState:
    def __init__(self):
        self._data: dict[str, Any] = {
            "notifications": [],
            "clipboard_text": "",
            "clipboard_history": [],
            "call_state": {},
            "call_ui_state": {},
            "audio_redirect_enabled": False,
            "call_audio_active": False,
            "call_route_status": "phone",
            "call_route_reason": "",
            "call_route_backend": "none",
            "sms_threads": [],
            "sms_draft_number": "",
            "connection_status": {},
            "connectivity_status": {},
            "connectivity_ops_busy": {},
            "ui_toast_queue": [],
            "notif_revision": {},
            "outbound_call_origin": {},
            "mobile_data_auto_paused": [],
        }
        self._listeners: dict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        for listener in list(self._listeners.get(key, [])):
            if self._should_queue_listener():
                _QT_BRIDGE.emit(listener, value)
                continue
            try:
                listener(value)
            except Exception:
                pass

    def subscribe(self, key: str, callback: Callable[[Any], None]) -> None:
        self._listeners[key].append(callback)

    @staticmethod
    def _should_queue_listener() -> bool:
        if _QT_BRIDGE is None or QThread is None or QApplication is None:
            return False
        try:
            if QApplication.instance() is None:
                return False
            return QThread.currentThread() is not _QT_BRIDGE.thread()
        except Exception:
            return False


state = AppState()
