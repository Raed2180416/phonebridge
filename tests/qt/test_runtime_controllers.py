"""Headless controller tests for extracted window runtime controllers."""

from __future__ import annotations

import importlib
import sys

import pytest

pytestmark = pytest.mark.qt_runtime

@pytest.fixture
def app():
    sys.modules.pop("PyQt6", None)
    sys.modules.pop("PyQt6.QtCore", None)
    sys.modules.pop("PyQt6.QtWidgets", None)
    qtwidgets = pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
    if not hasattr(qtwidgets, "QApplication"):
        pytest.skip("PyQt6 QtCore stubbed by another test module")
    QApplication = qtwidgets.QApplication
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


def test_call_controller_adapts_interval_by_state_and_visibility(app):
    sys.modules.pop("ui.runtime_controllers", None)
    controllers = importlib.import_module("ui.runtime_controllers")
    polls = []
    controller = controllers.CallController(app, lambda: polls.append("poll"))

    controller.start(visible=False)
    assert controller.mode() == "idle_hidden"
    assert controller.interval_ms() == controller.IDLE_HIDDEN_INTERVAL_MS

    controller.note_polled_state("unknown")
    assert controller.mode() == "degraded"
    assert controller.interval_ms() == controller.DEGRADED_INTERVAL_MS

    controller.note_signal_event("ringing")
    assert controller.mode() == "active"
    assert controller.interval_ms() == controller.ACTIVE_INTERVAL_MS

    controller.set_window_visible(True)
    controller.note_signal_event("ended")
    assert controller.mode() == "idle_visible"
    assert controller.interval_ms() == controller.IDLE_VISIBLE_INTERVAL_MS

    controller.stop()


def test_health_and_connectivity_controllers_manage_timers(app):
    sys.modules.pop("ui.runtime_controllers", None)
    controllers = importlib.import_module("ui.runtime_controllers")
    events = []
    health = controllers.HealthController(
        app,
        lambda: events.append("kde"),
        lambda: events.append("service"),
    )
    connectivity = controllers.ConnectivityController(app, lambda: events.append("policy"))

    connectivity.start(immediate=False)
    assert connectivity._timer.isActive()

    health.start()
    assert health._kde_timer.isActive()
    assert health._service_timer.isActive()

    health.suspend()
    assert not health._kde_timer.isActive()
    assert not health._service_timer.isActive()

    connectivity.stop()
    assert not connectivity._timer.isActive()


def test_notification_controller_schedules_startup_callbacks(monkeypatch, app):
    sys.modules.pop("ui.runtime_controllers", None)
    controllers = importlib.import_module("ui.runtime_controllers")
    scheduled = []

    monkeypatch.setattr(
        controllers.QTimer,
        "singleShot",
        staticmethod(lambda delay, callback: scheduled.append((int(delay), callback))),
    )

    controller = controllers.NotificationController(app, lambda: None, lambda: None)
    controller.prime_startup()

    assert [delay for delay, _callback in scheduled] == [900, 1200]


def test_clipboard_controller_records_remote_and_local_text(monkeypatch, app):
    sys.modules.pop("ui.runtime_controllers", None)
    controllers = importlib.import_module("ui.runtime_controllers")
    writes = []
    state_writes = []

    def _fake_get(key, default=None):
        if key == "clipboard_history":
            return []
        if key == "clipboard_autoshare":
            return True
        return default

    monkeypatch.setattr(controllers.settings, "get", _fake_get)
    monkeypatch.setattr(controllers.settings, "set", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(controllers.state, "set", lambda key, value: state_writes.append((key, value)))

    controller = controllers.ClipboardController(app)
    controller.apply_remote_text("from-phone")
    assert ("clipboard_text", "from-phone") in state_writes

    monkeypatch.setattr(controller, "_read_current_text", lambda: "from-pc")
    controller._on_local_clipboard_changed()
    assert ("clipboard_text", "from-pc") in state_writes
    assert any(key == "clipboard_history" for key, _value in writes)


def test_call_popup_module_imports_under_qt_runtime(app):
    sys.modules.pop("ui.components.call_popup", None)
    popup = importlib.import_module("ui.components.call_popup")
    assert hasattr(popup, "CallPopup")
