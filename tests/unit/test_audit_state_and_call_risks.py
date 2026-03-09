"""Audit coverage for state semantics and call-surface risk patterns.

These tests are intentionally written as audit assertions: they pass when they
prove the current behavior or unsafe pattern exists without changing production
code.
"""

from __future__ import annotations

import ast
import importlib
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _method_source(rel_path: str, method_name: str) -> str:
    source = (ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            segment = ast.get_source_segment(source, node)
            if segment is None:
                break
            return textwrap.dedent(segment)
    raise AssertionError(f"Could not find {method_name} in {rel_path}")


def test_app_state_get_exposes_live_mutable_reference_without_listener_notification():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    observed = []
    app_state.subscribe("notifications", lambda value: observed.append(list(value or [])))

    original = [{"id": "n1"}]
    app_state.set("notifications", original)
    observed.clear()

    leaked = app_state.get("notifications")
    leaked.append({"id": "n2"})

    assert app_state.get("notifications") == [{"id": "n1"}, {"id": "n2"}]
    assert observed == []


def test_app_state_sequential_multi_key_updates_expose_torn_snapshot_to_listeners():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    app_state.set("call_route_status", "phone")
    app_state.set("call_audio_active", False)
    snapshots = []

    def _listener(value):
        snapshots.append((value, app_state.get("call_audio_active")))

    app_state.subscribe("call_route_status", _listener)
    app_state.set("call_route_status", "pending_pc")
    app_state.set("call_audio_active", True)

    assert snapshots == [("pending_pc", False)]
    assert app_state.get("call_audio_active") is True


def test_app_state_reentrant_listener_updates_deliver_nested_notifications_immediately():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    events = []

    def _on_route(value):
        events.append(("route", value))
        app_state.set("call_route_reason", "Preparing laptop call audio...")

    def _on_reason(value):
        events.append(("reason", value))

    app_state.subscribe("call_route_status", _on_route)
    app_state.subscribe("call_route_reason", _on_reason)
    app_state.set("call_route_status", "pending_pc")

    assert events == [
        ("route", "pending_pc"),
        ("reason", "Preparing laptop call audio..."),
    ]


def test_call_popup_actions_run_sync_backend_calls_on_the_ui_path():
    action_expectations = {
        "answer_call": "ADBBridge().answer_call()",
        "reject_call": "ADBBridge().end_call()",
        "end_call": "ADBBridge().end_call()",
        "toggle_mute": "call_controls.set_call_muted(desired)",
        "call_back": "ADBBridge()._run(",
        "sms_reply_diversion_flow": "ADBBridge().end_call()",
    }
    for method_name, expected in action_expectations.items():
        method_src = _method_source("ui/components/call_popup_session.py", method_name)
        assert expected in method_src
        assert "threading.Thread(" not in method_src


def test_call_popup_close_event_uses_terminate_as_fallback_for_route_worker():
    method_src = _method_source("ui/components/call_popup_session.py", "closeEvent")
    assert "worker.requestInterruption()" in method_src
    assert "worker.wait(800)" in method_src
    assert "worker.terminate()" in method_src


def test_calls_page_route_worker_cancel_path_terminates_running_thread():
    method_src = _method_source("ui/pages/calls.py", "_cancel_call_route_worker")
    assert "worker.requestInterruption()" in method_src
    assert "worker.terminate()" in method_src
    assert "worker.wait(" not in method_src


def test_calls_page_route_result_handler_reads_shared_context_without_worker_identity():
    method_src = _method_source("ui/pages/calls.py", "_on_call_route_done")
    assert "ctx = dict(self._call_route_context or {})" in method_src
    assert "worker" not in method_src.splitlines()[0]

