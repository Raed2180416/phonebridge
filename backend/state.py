"""Simple in-memory app state and pub/sub hooks."""
from __future__ import annotations

from collections import defaultdict
import copy
import itertools
import logging
import threading
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
                log.exception("AppState Qt bridge listener failed")


    _QT_BRIDGE = _QtDispatchBridge()
else:
    _QT_BRIDGE = None


log = logging.getLogger(__name__)


class AppState:
    def __init__(self):
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "notifications": [],
            "clipboard_text": "",
            "clipboard_history": [],
            "call_state": {},
            "call_ui_state": {},
            "call_contacts_cache": [],
            "recent_calls_cache": [],
            "call_origin": "unknown",
            "call_local_end_action": "",
            "call_muted": False,
            "audio_redirect_enabled": False,
            "call_audio_active": False,
            "call_route_status": "phone",
            "call_route_reason": "",
            "call_route_backend": "none",
            "call_route_ui_state": {
                "status": "phone",
                "speaker_target": "Phone",
                "mic_target": "Phone",
                "reason": "",
                "mute_available": False,
                "mute_active": False,
                "updated_at": 0,
            },
            "sms_threads": [],
            "sms_draft_number": "",
            "connection_status": {},
            "connectivity_status": {},
            "connectivity_ops_busy": {},
            "connectivity_active_op": "",
            "ui_toast_queue": [],
            "notif_revision": {},
            "notif_open_request": {},
            "outbound_call_origin": {},
            "mobile_data_auto_paused": [],
            "service_health": {},
            "kde_health": {},
        }
        self._listeners: dict[str, dict[int, Callable[[Any], None]]] = defaultdict(dict)
        self._listener_ids = itertools.count(1)
        self._dispatch_depth = 0
        self._pending_notifications: list[tuple[str, Any, list[tuple[int, Callable[[Any], None]]]]] = []

    @staticmethod
    def _clone_value(value: Any) -> Any:
        if isinstance(value, (dict, list, set, tuple)):
            try:
                return copy.deepcopy(value)
            except Exception:
                log.debug("AppState deep copy failed; falling back to original value", exc_info=True)
        return value

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self._data.get(key, default)
        return self._clone_value(value)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            stored = self._clone_value(value)
            self._data[key] = stored
            listeners = list(self._listeners.get(key, {}).items())
        self._queue_notifications([(key, self._clone_value(stored), listeners)])

    def set_many(self, values: dict[str, Any]) -> None:
        notifications: list[tuple[str, Any, list[tuple[int, Callable[[Any], None]]]]] = []
        with self._lock:
            for key, raw_value in dict(values or {}).items():
                stored = self._clone_value(raw_value)
                self._data[key] = stored
                listeners = list(self._listeners.get(key, {}).items())
                notifications.append((key, self._clone_value(stored), listeners))
        self._queue_notifications(notifications)

    def update(self, key: str, updater: Callable[[Any], Any], default: Any = None) -> Any:
        with self._lock:
            current = self._data.get(key, default)
            working = self._clone_value(current)
            updated = updater(working)
            value = working if updated is None else updated
            stored = self._clone_value(value)
            self._data[key] = stored
            listeners = list(self._listeners.get(key, {}).items())
        self._queue_notifications([(key, self._clone_value(stored), listeners)])
        return self._clone_value(stored)

    def subscribe(self, key: str, callback: Callable[[Any], None], *, owner: Any = None) -> Callable[[], None]:
        with self._lock:
            listener_id = next(self._listener_ids)
            self._listeners[key][listener_id] = callback

        def _unsubscribe() -> None:
            with self._lock:
                bucket = self._listeners.get(key)
                if not bucket:
                    return
                bucket.pop(listener_id, None)
                if not bucket:
                    self._listeners.pop(key, None)

        if owner is not None:
            destroyed = getattr(owner, "destroyed", None)
            if destroyed is not None and hasattr(destroyed, "connect"):
                try:
                    destroyed.connect(lambda *_args: _unsubscribe())
                except Exception:
                    log.debug("Failed wiring AppState auto-unsubscribe key=%s", key, exc_info=True)
        return _unsubscribe

    def listener_count(self, key: str | None = None) -> int:
        with self._lock:
            if key is not None:
                return len(self._listeners.get(key, {}))
            return sum(len(bucket) for bucket in self._listeners.values())

    def _notify_listeners(
        self,
        key: str,
        value: Any,
        listeners: list[tuple[int, Callable[[Any], None]]],
    ) -> None:
        for _listener_id, listener in listeners:
            if self._should_queue_listener():
                self._emit_queued(key, listener, value)
                continue
            try:
                listener(value)
            except Exception:
                log.exception("AppState listener failed key=%s", key)

    def _emit_queued(self, key: str, callback: Callable[[Any], None], value: Any) -> None:
        if _QT_BRIDGE is None:
            try:
                callback(value)
            except Exception:
                log.exception("AppState queued listener failed key=%s", key)
            return

        def _deliver(queued_value: Any) -> None:
            try:
                callback(queued_value)
            except Exception:
                log.exception("AppState queued listener failed key=%s", key)

        _QT_BRIDGE.emit(_deliver, value)

    def _queue_notifications(
        self,
        notifications: list[tuple[str, Any, list[tuple[int, Callable[[Any], None]]]]],
    ) -> None:
        if not notifications:
            return
        with self._lock:
            self._pending_notifications.extend(notifications)
            if self._dispatch_depth:
                return
            self._dispatch_depth = 1
        while True:
            with self._lock:
                if not self._pending_notifications:
                    self._dispatch_depth = 0
                    return
                key, value, listeners = self._pending_notifications.pop(0)
            self._notify_listeners(key, value, listeners)

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
