"""Simple in-memory app state and pub/sub hooks."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable


class AppState:
    def __init__(self):
        self._data: dict[str, Any] = {
            "notifications": [],
            "clipboard_text": "",
            "clipboard_history": [],
            "call_state": {},
            "call_ui_state": {},
            "sms_threads": [],
            "sms_draft_number": "",
            "connection_status": {},
            "connectivity_status": {},
            "ui_toast_queue": [],
        }
        self._listeners: dict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        for listener in list(self._listeners.get(key, [])):
            try:
                listener(value)
            except Exception:
                pass

    def subscribe(self, key: str, callback: Callable[[Any], None]) -> None:
        self._listeners[key].append(callback)


state = AppState()
