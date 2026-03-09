"""Regression tests for default call-audio routing behavior.

Scenarios:
- Calls initiated on phone stay on phone by default.
- Calls initiated from Calls page auto-route to laptop once active.
- Window fallback telephony poll suspends/restores global media audio routing.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
import pytest

pytestmark = pytest.mark.qt_runtime

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)

from backend.state import state
from backend.call_routing import plan_polled_call_state
from ui.pages.calls import CallsPage


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = str(text)


def test_window_poll_suspends_and_restores_media_route(monkeypatch):
    calls = []

    def _fake_sync(*, suspend_ui_global=False, adb=None):
        calls.append(bool(suspend_ui_global))
        return True

    fake = SimpleNamespace(
        _last_polled_call_state="unknown",
        _call_state_route_suspended=False,
    )

    for raw_state in ("ringing", "idle"):
        plan = plan_polled_call_state(
            raw_state,
            previous_state=fake._last_polled_call_state,
            route_suspended=fake._call_state_route_suspended,
            call_ui={},
            suppress_calls=False,
            now_s=1.0,
        )
        if plan.sync_audio_suspend:
            _fake_sync(suspend_ui_global=True)
        elif plan.sync_audio_restore:
            _fake_sync(suspend_ui_global=False)
        fake._last_polled_call_state = plan.call_state
        fake._call_state_route_suspended = plan.next_route_suspended

    assert calls == [True, False]


def test_polled_call_plan_synthesizes_ringing_for_stale_ui():
    plan = plan_polled_call_state(
        "ringing",
        previous_state="idle",
        route_suspended=False,
        call_ui={
            "status": "ringing",
            "number": "+123",
            "contact_name": "Alice",
            "updated_at": 0,
        },
        suppress_calls=False,
        now_s=12.0,
    )
    assert plan.action == ""
    assert plan.sync_audio_suspend is True


def test_calls_page_incoming_resets_pc_route_request(monkeypatch):
    # Make sure stale outbound intent cannot hijack an incoming phone-origin call.
    state.set("outbound_call_origin", {})
    state.set("call_audio_active", False)

    route_attempts = []
    fake = SimpleNamespace(
        _call_state_hint=_Label(),
        _call_started=False,
        _pc_route_requested=True,
        _pc_route_retry_after_connect_done=False,
        _call_route_worker=None,
        _call_muted=False,
        adb=SimpleNamespace(set_call_muted=lambda _v: True),
        _set_local_mic_mute=lambda _v: True,
        _update_live_controls=lambda: None,
        _start_call_route_attempt=lambda number, who, intent: route_attempts.append((number, who, intent)),
    )

    CallsPage._on_call_ui_state_changed(
        fake,
        {
            "status": "ringing",
            "number": "+123",
            "contact_name": "Alice",
        },
    )

    assert fake._pc_route_requested is False
    assert fake._pc_route_retry_after_connect_done is False
    assert route_attempts == []


def test_calls_page_outbound_active_auto_routes_on_talking(monkeypatch):
    now_ms = 1_000_000
    calls_mod = importlib.import_module("ui.pages.calls")
    monkeypatch.setattr(calls_mod.time, "time", lambda: now_ms / 1000.0)

    state.set(
        "outbound_call_origin",
        {
            "source": "calls_page",
            "number": "+123",
            "ts_ms": now_ms,
            "active": True,
        },
    )
    state.set("call_audio_active", False)

    route_attempts = []
    fake = SimpleNamespace(
        _call_state_hint=_Label(),
        _call_started=False,
        _pc_route_requested=True,
        _pc_route_retry_after_connect_done=False,
        _call_route_worker=None,
        _call_muted=False,
        adb=SimpleNamespace(set_call_muted=lambda _v: True),
        _set_local_mic_mute=lambda _v: True,
        _update_live_controls=lambda: None,
        _start_call_route_attempt=lambda number, who, intent: route_attempts.append((number, who, intent)),
    )

    CallsPage._on_call_ui_state_changed(
        fake,
        {
            "status": "talking",
            "number": "+123",
            "contact_name": "Alice",
        },
    )

    assert fake._pc_route_retry_after_connect_done is True
    assert route_attempts == [('+123', 'Alice', 'outbound_auto')]
    assert state.get("call_route_status") == "pending_pc"
