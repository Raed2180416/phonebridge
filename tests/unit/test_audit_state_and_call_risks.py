"""Audit coverage for remediated state semantics and call-surface safety."""

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


def test_app_state_get_returns_isolated_mutable_copy_and_preserves_listener_contract():
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

    assert app_state.get("notifications") == [{"id": "n1"}]
    assert observed == []


def test_app_state_set_many_updates_related_keys_atomically_for_listeners():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    app_state.set("call_route_status", "phone")
    app_state.set("call_audio_active", False)
    snapshots = []

    def _listener(value):
        snapshots.append((value, app_state.get("call_audio_active")))

    app_state.subscribe("call_route_status", _listener)
    app_state.set_many(
        {
            "call_route_status": "pending_pc",
            "call_audio_active": True,
        }
    )

    assert snapshots == [("pending_pc", True)]
    assert app_state.get("call_audio_active") is True


def test_app_state_reentrant_listener_updates_are_deferred_until_outer_listener_returns():
    sys.modules.pop("backend.state", None)
    AppState = importlib.import_module("backend.state").AppState
    app_state = AppState()
    events = []

    def _on_route(value):
        events.append(("route", value))
        app_state.set("call_route_reason", "Preparing laptop call audio...")
        events.append(("route_after_set", app_state.get("call_route_reason")))

    def _on_reason(value):
        events.append(("reason", value))

    app_state.subscribe("call_route_status", _on_route)
    app_state.subscribe("call_route_reason", _on_reason)
    app_state.set("call_route_status", "pending_pc")

    assert events == [
        ("route", "pending_pc"),
        ("route_after_set", "Preparing laptop call audio..."),
        ("reason", "Preparing laptop call audio..."),
    ]


def test_call_popup_actions_dispatch_backend_work_async_and_do_not_call_adb_inline():
    action_expectations = {
        "answer_call": "call_controls.answer_call()",
        "reject_call": "call_controls.end_call()",
        "end_call": "call_controls.end_call()",
        "toggle_mute": "call_controls.set_call_muted(desired)",
        "call_back": "call_controls.place_call(number)",
        "sms_reply_diversion_flow": "call_controls.end_call()",
    }
    for method_name, backend_call in action_expectations.items():
        method_src = _method_source("ui/components/call_popup_session.py", method_name)
        assert "_start_popup_action(" in method_src
        assert backend_call in method_src
        assert "ADBBridge()." not in method_src


def test_call_popup_close_event_uses_cooperative_worker_shutdown_only():
    method_src = _method_source("ui/components/call_popup_session.py", "closeEvent")
    assert "worker.requestInterruption()" in method_src
    assert 'audio_route.set_source("call_pc_active", False)' in method_src
    assert "worker.wait(800)" in method_src
    assert "worker.terminate()" not in method_src


def test_calls_page_route_worker_cancel_path_uses_request_id_and_cooperative_shutdown():
    method_src = _method_source("ui/pages/calls.py", "_cancel_call_route_worker")
    assert "self._call_route_request_id += 1" in method_src
    assert "worker.requestInterruption()" in method_src
    assert 'audio_route.set_source("call_pc_active", False)' in method_src
    assert "worker.wait(800)" in method_src
    assert "worker.terminate()" not in method_src


def test_calls_page_route_result_handler_is_bound_to_request_id_and_worker_context():
    method_src = _method_source("ui/pages/calls.py", "_on_call_route_done")
    assert "request_id" in method_src.splitlines()[0]
    assert "worker" in method_src.splitlines()[0]
    assert 'ctx = dict(getattr(worker, "_request_context", {}) or {})' in method_src
    assert "_call_route_context" not in method_src
