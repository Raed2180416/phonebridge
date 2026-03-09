"""Lifecycle tests for DBusSignalBridge reconnect and teardown behavior."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

pytest.importorskip("PyQt6.QtCore")


def _install_stub(name: str, module: types.ModuleType, saved: dict[str, object | None]) -> None:
    saved.setdefault(name, sys.modules.get(name))
    sys.modules[name] = module


def test_signal_bridge_reconnect_disconnects_old_receivers(monkeypatch):
    saved: dict[str, object | None] = {}
    for mod_name in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"):
        saved[mod_name] = sys.modules.get(mod_name)
    saved["ui"] = sys.modules.get("ui")
    saved["ui.window"] = sys.modules.get("ui.window")
    try:
        for mod_name in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"):
            sys.modules.pop(mod_name, None)
        pyqt6 = types.ModuleType("PyQt6")
        pyqt6_core = types.ModuleType("PyQt6.QtCore")
        pyqt6_gui = types.ModuleType("PyQt6.QtGui")
        pyqt6_widgets = types.ModuleType("PyQt6.QtWidgets")

        class _FakeSignal:
            def connect(self, *_a, **_kw): pass
            def emit(self, *_a, **_kw): pass
            def disconnect(self, *_a, **_kw): pass

        class _FakeQObject:
            def __init__(self, *_a, **_kw): pass

        class _FakeTimer:
            def __init__(self, *_a, **_kw): pass
            def start(self, *_a, **_kw): pass
            def stop(self): pass
            def isActive(self): return False
            timeout = _FakeSignal()
            @staticmethod
            def singleShot(*_a, **_kw): pass

        pyqt6_core.QObject = _FakeQObject
        pyqt6_core.QTimer = _FakeTimer
        pyqt6_core.pyqtSignal = lambda *a, **kw: _FakeSignal()
        pyqt6_core.pyqtProperty = lambda *a, **kw: property(lambda self: None, lambda self, value: None)
        for attr in ("Qt", "QPoint", "QPropertyAnimation", "QEasingCurve"):
            setattr(pyqt6_core, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))

        for attr in (
            "QMainWindow", "QWidget", "QHBoxLayout", "QVBoxLayout", "QPushButton",
            "QStackedWidget", "QLabel", "QScrollArea", "QApplication", "QFrame",
        ):
            setattr(pyqt6_widgets, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))

        for attr in ("QPainter", "QColor", "QFont", "QClipboard"):
            setattr(pyqt6_gui, attr, type(attr, (), {"__init__": lambda self, *a, **kw: None}))

        sys.modules["PyQt6"] = pyqt6
        sys.modules["PyQt6.QtCore"] = pyqt6_core
        sys.modules["PyQt6.QtGui"] = pyqt6_gui
        sys.modules["PyQt6.QtWidgets"] = pyqt6_widgets
        ui_root = sys.modules.get("ui")
        if ui_root is not None and not hasattr(ui_root, "__path__"):
            sys.modules.pop("ui", None)
        theme = types.ModuleType("ui.theme")
        theme.TEAL = "#0ff"
        theme.VIOLET = "#f0f"
        theme.BORDER = "#111"
        theme.TEXT_DIM = "#999"
        theme.SIDEBAR_STYLE = ""
        theme.with_alpha = lambda value, _alpha=1.0: value
        theme.get_app_style = lambda *_a, **_kw: ""
        theme.set_surface_alpha = lambda *_a, **_kw: None
        theme.refresh_card_styles = lambda *_a, **_kw: None
        theme.set_theme_name = lambda *_a, **_kw: None
        _install_stub("ui.theme", theme, saved)

        motion = types.ModuleType("ui.motion")
        motion.fade_in = lambda *_a, **_kw: None
        _install_stub("ui.motion", motion, saved)

        state_mod = types.ModuleType("backend.state")
        state_mod.state = types.SimpleNamespace(get=lambda *_a, **_kw: {}, set=lambda *_a, **_kw: None, subscribe=lambda *_a, **_kw: None)
        _install_stub("backend.state", state_mod, saved)

        settings_mod = types.ModuleType("backend.settings_store")
        settings_mod.get = lambda *_a, **_kw: False
        settings_mod.set = lambda *_a, **_kw: None
        _install_stub("backend.settings_store", settings_mod, saved)

        _install_stub("backend.audio_route", types.ModuleType("backend.audio_route"), saved)

        call_routing = types.ModuleType("backend.call_routing")
        call_routing.normalize_call_event = lambda value: value
        call_routing.outbound_origin_active = lambda *_a, **_kw: False
        call_routing.notification_reason_can_synthesize = lambda *_a, **_kw: False
        call_routing.should_attempt_notification_call_synthesis = lambda *_a, **_kw: False
        call_routing.allow_call_hint_when_recent_idle = lambda *_a, **_kw: True
        call_routing.build_call_route_ui_state = lambda **_kw: {}
        call_routing.finalize_pending_call_session = lambda *_a, **_kw: types.SimpleNamespace(session=None)
        call_routing.is_redundant_live_call_event = lambda *_a, **_kw: False
        call_routing.meaningful_call_display_name = lambda value, *_a, **_kw: str(value or "")
        call_routing.phone_match_key = lambda value: str(value or "")
        call_routing.plan_polled_call_state = lambda *_a, **_kw: types.SimpleNamespace(
            call_state="idle",
            state_changed=False,
            next_route_suspended=False,
            should_synthesize_from_notifications=False,
            sync_audio_suspend=False,
            sync_audio_restore=False,
            action="",
            number="",
            contact_name="",
        )
        call_routing.reduce_call_session = lambda *_a, **_kw: types.SimpleNamespace(session=None)
        call_routing.resolve_call_display_name = lambda *_a, **_kw: ""
        call_routing.seed_outbound_call_session = lambda *_a, **_kw: types.SimpleNamespace(
            phase="dialing",
            number="",
            display_name="",
            to_public_row=lambda: {},
        )
        _install_stub("backend.call_routing", call_routing, saved)

        clipboard = types.ModuleType("backend.clipboard_history")
        clipboard.sanitize_clipboard_history = lambda value: value
        _install_stub("backend.clipboard_history", clipboard, saved)

        syncthing = types.ModuleType("backend.syncthing")
        syncthing.Syncthing = type("Syncthing", (), {})
        _install_stub("backend.syncthing", syncthing, saved)

        notification_mirror = types.ModuleType("backend.notification_mirror")
        notification_mirror.sync_desktop_notifications = lambda *_a, **_kw: None
        _install_stub("backend.notification_mirror", notification_mirror, saved)

        notifications_state = types.ModuleType("backend.notifications_state")
        notifications_state.normalize_notifications = lambda rows: rows
        notifications_state.phone_call_notification_key = lambda *_a, **_kw: ""
        notifications_state.record_dismissed_many = lambda *_a, **_kw: None
        notifications_state.record_hidden_call_keys = lambda *_a, **_kw: None
        _install_stub("backend.notifications_state", notifications_state, saved)

        adb = types.ModuleType("backend.adb_bridge")
        adb.ADBBridge = type("ADBBridge", (), {"__init__": lambda self, *_a, **_kw: None})
        _install_stub("backend.adb_bridge", adb, saved)

        for module_name, class_name in (
            ("ui.pages.dashboard", "DashboardPage"),
            ("ui.pages.messages", "MessagesPage"),
            ("ui.pages.calls", "CallsPage"),
            ("ui.pages.files", "FilesPage"),
            ("ui.pages.mirror", "MirrorPage"),
            ("ui.pages.network", "NetworkPage"),
            ("ui.pages.settings", "SettingsPage"),
        ):
            page_mod = types.ModuleType(module_name)
            setattr(page_mod, class_name, type(class_name, (), {}))
            _install_stub(module_name, page_mod, saved)

        class _FakeKC:
            def __init__(self, label: str):
                self.label = label
                self.connected = []
                self.disconnects = 0

            def connect_call_signal(self, callback):
                self.connected.append(("call", callback))

            def connect_notification_signal(self, **callbacks):
                self.connected.append(("notification", callbacks))

            def connect_battery_signal(self, callback):
                self.connected.append(("battery", callback))

            def connect_clipboard_signal(self, callback):
                self.connected.append(("clipboard", callback))

            def disconnect_all_signals(self):
                self.disconnects += 1

        old_kc = _FakeKC("old")
        new_kc = _FakeKC("new")
        suppress_calls = []

        class _KDEConnect:
            @staticmethod
            def suppress_native_notification_popups(enabled):
                suppress_calls.append(bool(enabled))
                return True

            def __new__(cls):
                return new_kc

        kdeconnect = types.ModuleType("backend.kdeconnect")
        kdeconnect.KDEConnect = _KDEConnect
        _install_stub("backend.kdeconnect", kdeconnect, saved)

        win = importlib.import_module("ui.window")
        win = importlib.reload(win)

        bridge = win.DBusSignalBridge()
        bridge._running = True
        bridge._kc = old_kc

        bridge._on_kde_name_owner_changed(":1.204")

        assert old_kc.disconnects == 1
        assert [name for name, _payload in new_kc.connected] == ["call", "notification", "battery", "clipboard"]
        assert suppress_calls == [True]

        bridge.stop()
        assert new_kc.disconnects == 1
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
