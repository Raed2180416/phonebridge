"""Qt regression coverage for cleaned-up call/settings/dashboard surfaces."""

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
    QApplication = qtwidgets.QApplication
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


def _collect_texts(widget):
    qtwidgets = importlib.import_module("PyQt6.QtWidgets")
    texts = []
    for cls_name in ("QLabel", "QPushButton"):
        cls = getattr(qtwidgets, cls_name)
        texts.extend(str(child.text()) for child in widget.findChildren(cls))
    return texts


def _state():
    return importlib.import_module("backend.state").state


def test_window_page_registry_drops_sync_page(app):
    sys.modules.pop("ui.window", None)
    window = importlib.import_module("ui.window")

    page_ids = [page_id for _icon, _name, _page_cls, page_id in window.PAGES]

    assert "sync" not in page_ids


def test_calls_page_has_no_live_end_mute_or_route_buttons(monkeypatch, app):
    sys.modules.pop("ui.pages.calls", None)
    calls = importlib.import_module("ui.pages.calls")

    class _KC:
        def get_cached_contacts(self):
            return []

        def sync_contacts(self):
            return None

    class _ADB:
        def __init__(self, *_a, **_kw):
            self.target = ""

        def get_recent_calls(self, limit=40):
            return []

        def get_contacts(self, limit=500):
            return []

        def _run(self, *_a, **_kw):
            return ""

    monkeypatch.setattr(calls, "KDEConnect", _KC)
    monkeypatch.setattr(calls, "ADBBridge", _ADB)
    monkeypatch.setattr(calls.CallsPage, "refresh", lambda self: None)

    page = calls.CallsPage()
    texts = _collect_texts(page)

    assert "End" not in texts
    assert "Mute" not in texts
    assert "End Call" not in texts
    assert "Switch to Laptop Audio" not in texts
    assert "Switch to Phone Audio" not in texts


def test_settings_page_keeps_missed_call_toggle_and_drops_theme_and_integration_controls(monkeypatch, app):
    sys.modules.pop("ui.pages.settings", None)
    settings_page = importlib.import_module("ui.pages.settings")

    monkeypatch.setattr(settings_page.autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(settings_page.call_audio, "list_output_devices", lambda: [])
    monkeypatch.setattr(settings_page.call_audio, "list_input_devices", lambda: [])
    monkeypatch.setattr(settings_page.call_audio, "output_volume_pct", lambda: 100)
    monkeypatch.setattr(settings_page.call_audio, "input_volume_pct", lambda: 100)

    page = settings_page.SettingsPage()
    texts = _collect_texts(page)

    assert any("Missed Call Popups" in text for text in texts)
    banned = {
        "Manage App Icon",
        "Manage Desktop Entry",
        "Manage Hyprland SUPER+P Bind",
        "Auto-Enable Start on Login",
        "Theme",
        "Toggle Keybind",
        "Integration Writes (Opt-in)",
        "Appearance",
    }
    assert not any(text in banned for text in texts)


def test_dashboard_quick_actions_drop_calls_panel(monkeypatch, app):
    sys.modules.pop("ui.pages.dashboard", None)
    dashboard = importlib.import_module("ui.pages.dashboard")

    class _KC:
        pass

    class _TS:
        pass

    class _ADB:
        pass

    class _BT:
        pass

    monkeypatch.setattr(dashboard, "KDEConnect", _KC)
    monkeypatch.setattr(dashboard, "Tailscale", _TS)
    monkeypatch.setattr(dashboard, "ADBBridge", _ADB)
    monkeypatch.setattr(dashboard, "BluetoothManager", _BT)
    monkeypatch.setattr(dashboard.audio_route, "sync", lambda *args, **kwargs: True)
    monkeypatch.setattr(dashboard.DashboardPage, "refresh", lambda self: None)

    page = dashboard.DashboardPage()
    texts = _collect_texts(page)

    assert "Calls Panel" not in texts


def test_call_popup_route_summary_and_mute_visibility_follow_call_route_ui_state(monkeypatch, app):
    sys.modules.pop("ui.components.call_popup", None)
    popup_mod = importlib.import_module("ui.components.call_popup")
    popup = popup_mod.CallPopup(None)
    popup.current_state = "talking"

    phone_route = {
        "status": "phone",
        "speaker_target": "Phone",
        "mic_target": "Phone",
        "reason": "",
        "mute_available": False,
        "mute_active": False,
        "updated_at": 1,
    }
    _state().set("call_route_ui_state", phone_route)
    popup._on_call_route_ui_state_changed(phone_route)
    assert popup.route_summary_label.text() == "Speaker: Phone · Mic: Phone"
    assert popup.secondary_btn.isHidden() is True

    laptop_route = {
        "status": "laptop",
        "speaker_target": "Laptop",
        "mic_target": "Laptop",
        "reason": "Audio on laptop/PC",
        "mute_available": True,
        "mute_active": True,
        "updated_at": 2,
    }
    _state().set("call_muted", True)
    _state().set("call_route_ui_state", laptop_route)
    popup._on_call_route_ui_state_changed(laptop_route)
    popup._on_call_muted_changed(True)

    assert popup.route_summary_label.text() == "Speaker: Laptop · Mic: Laptop"
    assert popup.route_reason_label.text() == "Audio on laptop/PC"
    assert popup.secondary_btn.isHidden() is False
    assert popup.secondary_btn.text() == "Mute"
    assert popup.reply_btn.isHidden() is True


def test_call_popup_warmup_stays_hidden_until_activation(monkeypatch, app):
    sys.modules.pop("ui.components.call_popup", None)
    popup_mod = importlib.import_module("ui.components.call_popup")
    popup = popup_mod.CallPopup(None)

    monkeypatch.setattr(popup_mod.hyprland, "ensure_call_popup_rules", lambda: (True, "ok"))
    monkeypatch.setattr(popup_mod.hyprland, "move_window_exact", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(popup_mod.hyprland, "set_floating_pinned_top", lambda *_args, **_kwargs: True)

    popup.warmup_surface()
    assert popup.isVisible() is False
    assert popup.is_popup_active() is False
    assert popup.card.isVisible() is False
    assert popup.width() >= 300

    popup.handle_call_event("+1234567890", "Mom", "ringing")
    assert popup.isVisible() is True
    assert popup.is_popup_active() is True
    assert popup.card.isVisible() is True
    assert popup.width() >= 300

    popup.hide_popup()
    assert popup.isVisible() is False
    assert popup.is_popup_active() is False
    assert popup.card.isVisible() is False

    popup.handle_call_event("+1234567890", "Mom", "ringing")
    assert popup.isVisible() is True
    assert popup.is_popup_active() is True
    assert popup.card.isVisible() is True


def test_calls_page_places_call_without_sync_adb_block(monkeypatch, app):
    sys.modules.pop("ui.pages.calls", None)
    calls = importlib.import_module("ui.pages.calls")

    class _KC:
        def get_cached_contacts(self):
            return []

        def sync_contacts(self):
            return None

    class _ADB:
        def __init__(self, *_a, **_kw):
            self.target = ""

        def get_recent_calls(self, limit=40):
            return []

        def get_contacts(self, limit=500):
            return []

        def _run(self, *_a, **_kw):
            raise AssertionError("_run should not execute synchronously during _place_call")

    monkeypatch.setattr(calls, "KDEConnect", _KC)
    monkeypatch.setattr(calls, "ADBBridge", _ADB)
    monkeypatch.setattr(calls.CallsPage, "refresh", lambda self: None)

    launched = []
    page = calls.CallsPage()
    monkeypatch.setattr(page, "_launch_outbound_call_async", lambda number: launched.append(number))
    page._dial_input.setText("+15551234567")

    page._place_call()

    assert launched == ["+15551234567"]
    call_ui = _state().get("call_ui_state", {}) or {}
    assert call_ui.get("phase") == "dialing"
    assert call_ui.get("number") == "+15551234567"


def test_calls_page_background_workers_do_not_bind_stale_adb_target(monkeypatch, app):
    sys.modules.pop("ui.pages.calls", None)
    calls = importlib.import_module("ui.pages.calls")

    class _KC:
        def get_cached_contacts(self):
            return []

        def sync_contacts(self):
            return None

    class _ADB:
        def __init__(self, *_a, **_kw):
            self.target = "STALE_SERIAL"

    class _Worker:
        def __init__(self, target, limit=0):
            self.target = target
            self.limit = limit
            self.done = type("_Signal", (), {"connect": lambda *_a, **_kw: None})()
            self.finished = type("_Signal", (), {"connect": lambda *_a, **_kw: None})()

        def start(self):
            return None

        def deleteLater(self):
            return None

    monkeypatch.setattr(calls, "KDEConnect", _KC)
    monkeypatch.setattr(calls, "ADBBridge", _ADB)
    monkeypatch.setattr(calls.CallsPage, "refresh", lambda self: None)
    monkeypatch.setattr(calls, "ContactsWorker", _Worker)
    monkeypatch.setattr(calls, "CallsHistoryWorker", _Worker)

    page = calls.CallsPage()
    page._contacts_busy = False
    page._history_busy = False

    page._load_contacts()
    page._load_history_from_phone()

    assert page._contacts_worker.target == ""
    assert page._history_worker.target == ""


def test_calls_page_terminal_cleanup_clears_mute_async(monkeypatch, app):
    sys.modules.pop("ui.pages.calls", None)
    calls = importlib.import_module("ui.pages.calls")

    class _KC:
        def get_cached_contacts(self):
            return []

        def sync_contacts(self):
            return None

    class _ADB:
        def __init__(self, *_a, **_kw):
            self.target = ""

        def get_recent_calls(self, limit=40):
            return []

        def get_contacts(self, limit=500):
            return []

    monkeypatch.setattr(calls, "KDEConnect", _KC)
    monkeypatch.setattr(calls, "ADBBridge", _ADB)
    monkeypatch.setattr(calls.CallsPage, "refresh", lambda self: None)

    page = calls.CallsPage()
    cleared: list[str] = []
    monkeypatch.setattr(page, "_clear_call_mute_async", lambda: cleared.append("clear"))
    _state().set("call_muted", True)

    page._on_call_ui_state_changed({"phase": "ended", "status": "ended"})

    assert cleared == ["clear"]
