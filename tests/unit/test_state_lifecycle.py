"""Deterministic lifecycle tests for backend.state.AppState."""

from __future__ import annotations

import importlib
import sys


class _FakeDestroyedSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self):
        for callback in list(self._callbacks):
            callback()


class _FakeOwner:
    def __init__(self):
        self.destroyed = _FakeDestroyedSignal()


def test_subscribe_returns_unsubscribe_and_listener_count_drops():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    seen = []

    unsubscribe = app_state.subscribe("notifications", lambda value: seen.append(value))
    assert app_state.listener_count("notifications") == 1

    app_state.set("notifications", [{"id": "n1"}])
    assert seen == [[{"id": "n1"}]]

    unsubscribe()
    assert app_state.listener_count("notifications") == 0


def test_owner_destroyed_auto_unsubscribes():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    owner = _FakeOwner()
    seen = []

    app_state.subscribe("service_health", lambda value: seen.append(value), owner=owner)
    assert app_state.listener_count("service_health") == 1

    owner.destroyed.emit()
    assert app_state.listener_count("service_health") == 0

    app_state.set("service_health", {"overall": "ok"})
    assert seen == []


def test_update_clones_mutable_state_before_mutation():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    original = [{"id": "n1"}]
    app_state.set("notifications", original)

    updated = app_state.update(
        "notifications",
        lambda rows: rows.append({"id": "n2"}),
        default=[],
    )

    assert original == [{"id": "n1"}]
    assert updated == [{"id": "n1"}, {"id": "n2"}]
    assert app_state.get("notifications") == [{"id": "n1"}, {"id": "n2"}]
