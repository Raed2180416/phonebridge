from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtCore", exc_type=ImportError)


class _PageContainer:
    def __init__(self, page):
        self._page = page

    def widget(self):
        return self._page


class _Stack:
    def __init__(self, page):
        self._page = page

    def currentWidget(self):
        return _PageContainer(self._page)


class _StubTimer:
    def __init__(self):
        self.started_ms = None
        self.stopped = False

    def start(self, ms):
        self.started_ms = int(ms)

    def stop(self):
        self.stopped = True


def _state():
    return importlib.import_module("backend.state").state


def test_syncthing_runtime_status_refreshes_only_current_page_that_allows_it(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    calls: list[str] = []

    class _Page:
        allow_runtime_status_refresh = True

        def refresh(self):
            calls.append("refresh")

    page = _Page()
    dummy = window_runtime.WindowRuntimeMixin()
    dummy._stack = _Stack(page)

    monkeypatch.setattr(window_runtime.QTimer, "singleShot", staticmethod(lambda _delay, callback: callback()))
    _state().set("call_ui_state", {"phase": "ended"})
    dummy._on_syncthing_runtime_status({})

    assert calls == ["refresh"]


def test_syncthing_runtime_status_skips_non_refreshable_pages_and_active_calls(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    calls: list[str] = []

    class _Page:
        allow_runtime_status_refresh = False

        def refresh(self):
            calls.append("refresh")

    page = _Page()
    dummy = window_runtime.WindowRuntimeMixin()
    dummy._stack = _Stack(page)

    monkeypatch.setattr(window_runtime.QTimer, "singleShot", staticmethod(lambda _delay, callback: callback()))

    _state().set("call_ui_state", {"phase": "ended"})
    dummy._on_syncthing_runtime_status({})
    assert calls == []

    page.allow_runtime_status_refresh = True
    _state().set("call_ui_state", {"phase": "ringing"})
    dummy._on_syncthing_runtime_status({})
    assert calls == []

    _state().set("call_ui_state", {"phase": "talking"})
    dummy._on_syncthing_runtime_status({})
    assert calls == []


def test_notification_call_hint_enriches_active_generic_ringing_session(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    published: list[tuple[str, str, str, str, str]] = []
    dummy._call_session_state = SimpleNamespace(phase="ringing", number="", display_name="Incoming call")
    dummy._current_call_audio_target = lambda: "phone"
    dummy._publish_call_snapshot = lambda status, number, name, audio_target="phone", *, source="snapshot": published.append(
        (status, number, name, audio_target, source)
    )

    _state().set(
        "notifications",
        [
            {
                "app": "Phone",
                "title": "Mom",
                "text": "Incoming call",
                "actions": ["Answer", "Decline"],
            }
        ],
    )
    dummy._maybe_synthesize_call_from_notifications(trigger_reason="posted")
    assert published == [("ringing", "", "Mom", "phone", "notification")]


def test_notification_call_hint_does_not_create_new_session_when_idle(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    dummy._call_session_state = None
    published: list[tuple[str, str, str, str, str]] = []
    dummy._current_call_audio_target = lambda: "phone"
    dummy._publish_call_snapshot = lambda status, number, name, audio_target="phone", *, source="snapshot": published.append(
        (status, number, name, audio_target, source)
    )

    _state().set(
        "notifications",
        [
            {
                "id": "call-1",
                "app": "Phone",
                "title": "Mom",
                "text": "Incoming call",
                "actions": ["Answer", "Decline"],
            }
        ],
    )
    dummy._maybe_synthesize_call_from_notifications(trigger_reason="posted")
    assert published == []


def test_polled_ringing_edge_opens_popup_immediately(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    dummy._call_state_poll_busy = False
    dummy._last_polled_call_state = "idle"
    dummy._last_polled_at = 0.0
    dummy._last_non_unknown_polled_call_state = "idle"
    dummy._last_non_unknown_polled_at = 99.0
    dummy._call_state_route_suspended = False
    dummy._pending_poll_popup = None
    dummy._poll_popup_fallback_timer = _StubTimer()
    dummy._call_controller = None
    dummy._call_session_state = None
    dummy._terminal_idle_boundary_open = True
    dummy._awaiting_terminal_idle_boundary = False
    dummy._polled_live_candidate_state = ""
    dummy._polled_live_candidate_hits = 0
    dummy._polled_live_candidate_first_at = 0.0
    dummy._polled_live_candidate_last_at = 0.0
    sync_calls = []
    dummy._sync_audio_route_async = lambda **_kw: sync_calls.append(dict(_kw))
    immediate = []
    dummy._on_call_received = lambda event, number, name, **kwargs: immediate.append((event, number, name, kwargs.get("source")))

    _state().set("call_ui_state", {})
    monkeypatch.setattr(window_runtime.settings, "get", lambda key, default=None: False if key == "suppress_calls" else default)
    now = {"value": 100.0}
    monkeypatch.setattr(window_runtime.time, "time", lambda: now["value"])
    dummy._apply_polled_call_state("ringing")
    assert immediate == [("incoming_call", "", "Incoming call", "telephony_poll")]
    assert sync_calls == [{"suspend_ui_global": True}]

    now["value"] = 101.0
    dummy._apply_polled_call_state("ringing")

    assert immediate == [("incoming_call", "", "Incoming call", "telephony_poll")]
    assert sync_calls == [{"suspend_ui_global": True}, {"suspend_ui_global": True}]
    assert dummy._pending_poll_popup is None
    assert dummy._poll_popup_fallback_timer.started_ms is None


def test_polled_offhook_does_not_promote_active_session_to_talking(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    dummy._last_polled_call_state = "unknown"
    dummy._last_polled_at = 0.0
    dummy._call_state_route_suspended = False
    dummy._call_controller = None
    dummy._pending_poll_popup = None
    dummy._poll_popup_fallback_timer = _StubTimer()
    dummy._terminal_idle_boundary_open = True
    dummy._awaiting_terminal_idle_boundary = False
    dummy._polled_live_candidate_state = ""
    dummy._polled_live_candidate_hits = 0
    dummy._polled_live_candidate_first_at = 0.0
    dummy._polled_live_candidate_last_at = 0.0
    dummy._call_session_state = SimpleNamespace(phase="ringing", number="+123", display_name="Alice")
    sync_calls = []
    dummy._sync_audio_route_async = lambda **_kw: sync_calls.append(dict(_kw))
    received = []
    dummy._on_call_received = lambda event, number, name, **kwargs: received.append((event, number, name, kwargs.get("source")))

    _state().set(
        "call_ui_state",
        {
            "phase": "ringing",
            "status": "ringing",
            "number": "+123",
            "display_name": "Alice",
            "contact_name": "Alice",
            "updated_at": 99_000,
        },
    )
    monkeypatch.setattr(window_runtime.settings, "get", lambda key, default=None: False if key == "suppress_calls" else default)
    now = {"value": 100.0}
    monkeypatch.setattr(window_runtime.time, "time", lambda: now["value"])

    dummy._apply_polled_call_state("offhook")
    assert received == []
    assert sync_calls == [{"suspend_ui_global": True}]

    now["value"] = 101.0
    dummy._apply_polled_call_state("offhook")

    assert received == []
    assert sync_calls == [{"suspend_ui_global": True}, {"suspend_ui_global": True}]


def test_polled_ringing_reopens_only_after_idle_boundary(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    dummy._call_state_poll_busy = False
    dummy._last_polled_call_state = "idle"
    dummy._last_polled_at = 0.0
    dummy._last_non_unknown_polled_call_state = "idle"
    dummy._last_non_unknown_polled_at = 99.0
    dummy._call_state_route_suspended = False
    dummy._pending_poll_popup = None
    dummy._poll_popup_fallback_timer = _StubTimer()
    dummy._call_controller = None
    dummy._call_session_state = None
    dummy._terminal_idle_boundary_open = False
    dummy._awaiting_terminal_idle_boundary = True
    dummy._polled_live_candidate_state = ""
    dummy._polled_live_candidate_hits = 0
    dummy._polled_live_candidate_first_at = 0.0
    dummy._polled_live_candidate_last_at = 0.0
    dummy._sync_audio_route_async = lambda **_kw: None
    received = []
    dummy._on_call_received = lambda event, number, name, **kwargs: received.append((event, number, name, kwargs.get("source")))

    _state().set("call_ui_state", {})
    monkeypatch.setattr(window_runtime.settings, "get", lambda key, default=None: False if key == "suppress_calls" else default)
    now = {"value": 100.0}
    monkeypatch.setattr(window_runtime.time, "time", lambda: now["value"])

    dummy._apply_polled_call_state("ringing")
    now["value"] = 101.0
    dummy._apply_polled_call_state("ringing")
    assert received == []

    now["value"] = 102.0
    dummy._apply_polled_call_state("idle")
    assert dummy._terminal_idle_boundary_open is True

    now["value"] = 103.0
    dummy._apply_polled_call_state("ringing")
    now["value"] = 104.0
    dummy._apply_polled_call_state("ringing")

    assert received == [("incoming_call", "", "Incoming call", "telephony_poll")]


def test_polled_idle_finalizes_live_session_even_if_public_call_ui_row_is_stale(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    dummy._call_state_poll_busy = False
    dummy._last_polled_call_state = "offhook"
    dummy._last_polled_at = 0.0
    dummy._last_non_unknown_polled_call_state = "offhook"
    dummy._last_non_unknown_polled_at = 99.0
    dummy._call_state_route_suspended = False
    dummy._pending_poll_popup = None
    dummy._poll_popup_fallback_timer = _StubTimer()
    dummy._call_controller = None
    dummy._terminal_idle_boundary_open = True
    dummy._awaiting_terminal_idle_boundary = False
    dummy._polled_live_candidate_state = ""
    dummy._polled_live_candidate_hits = 0
    dummy._polled_live_candidate_first_at = 0.0
    dummy._polled_live_candidate_last_at = 0.0
    dummy._call_session_state = SimpleNamespace(
        phase="ringing",
        number="+123",
        display_name="Alice",
        updated_at_ms=90_000,
        pending_terminal="",
    )
    dummy._sync_audio_route_async = lambda **_kw: None
    received = []
    dummy._on_call_received = lambda event, number, name, **kwargs: received.append((event, number, name, kwargs.get("source")))

    _state().set("call_ui_state", {})
    monkeypatch.setattr(window_runtime.settings, "get", lambda key, default=None: False if key == "suppress_calls" else default)
    monkeypatch.setattr(window_runtime.time, "time", lambda: 100.0)

    dummy._apply_polled_call_state("idle")

    assert received == [("ended", "+123", "Alice", "verification")]


def test_resolve_call_display_name_skips_cache_when_number_missing(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    monkeypatch.setattr(dummy, "_call_contacts_cache", lambda: (_ for _ in ()).throw(AssertionError("contacts cache should not load")))
    monkeypatch.setattr(dummy, "_call_history_cache", lambda: (_ for _ in ()).throw(AssertionError("history cache should not load")))

    assert dummy._resolve_call_display_name("", "Incoming call") == "Incoming call"
    assert dummy._resolve_call_display_name("", "", previous_name="Mom") == "Mom"


def test_resolve_call_display_name_uses_explicit_name_without_loading_contacts(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    monkeypatch.setattr(dummy, "_call_contacts_cache", lambda: (_ for _ in ()).throw(AssertionError("contacts cache should not load")))
    monkeypatch.setattr(dummy, "_call_history_cache", lambda: (_ for _ in ()).throw(AssertionError("history cache should not load")))
    monkeypatch.setattr(dummy, "_prime_call_contacts_cache_async", lambda: (_ for _ in ()).throw(AssertionError("contacts prime should not start")))

    assert dummy._resolve_call_display_name("+15551234567", "Mom") == "Mom"


def test_resolve_call_display_name_primes_contacts_async_when_cache_empty(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    dummy = window_runtime.WindowRuntimeMixin()
    primes: list[str] = []
    _state().set("call_contacts_cache", [])
    _state().set("recent_calls_cache", [])
    _state().set("outbound_call_origin", {})
    monkeypatch.setattr(dummy, "_prime_call_contacts_cache_async", lambda: primes.append("prime"))

    assert dummy._resolve_call_display_name("+15551234567", "Incoming call", previous_name="Mom") == "Mom"
    assert primes == ["prime"]


def test_ended_call_decision_dismisses_popup_without_popup_event_handler(monkeypatch):
    sys.modules.pop("ui.window_runtime", None)
    if sys.modules.get("ui") is not None and not hasattr(sys.modules["ui"], "__path__"):
        sys.modules.pop("ui", None)
    try:
        window_runtime = importlib.import_module("ui.window_runtime")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for window_runtime import: {exc}")

    class _Popup:
        def __init__(self):
            self.dismissed = 0
            self.handled = []

        def dismiss_active_call(self):
            self.dismissed += 1

        def handle_call_event(self, number, name, event):
            self.handled.append((number, name, event))

        def isVisible(self):
            return True

    class _Session:
        phase = "ended"
        number = "+15551234567"
        display_name = "Alice"

        def to_public_row(self):
            return {
                "phase": "ended",
                "status": "ended",
                "number": self.number,
                "display_name": self.display_name,
                "updated_at": 1,
            }

    class _Decision:
        session = _Session()
        popup_event = "ended"
        history_event = "ended"
        publish = True
        clear_terminal_check = True
        schedule_terminal_check_ms = 0
        ignored = False

    dummy = window_runtime.WindowRuntimeMixin()
    dummy._call_terminal_timer = type("T", (), {"stop": lambda self: None, "start": lambda self, _ms: None})()
    dummy._call_session_state = None
    dummy._call_popup = _Popup()
    dummy._publish_call_session = lambda _session: None
    dummy._sync_calls_page_call = lambda *_args: None
    dummy._cancel_poll_popup_fallback = lambda: None
    monkeypatch.setattr(window_runtime.QTimer, "singleShot", staticmethod(lambda _delay, callback: callback()))

    dummy._apply_call_session_decision(_Decision())

    assert dummy._call_popup.dismissed == 1
    assert dummy._call_popup.handled == []
    assert dummy._last_terminal_call_fingerprint == "num:5551234567"
    assert dummy._last_terminal_notification_updated_at == 0
