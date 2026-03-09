"""Audit coverage for remediated runtime and connectivity behavior."""

from __future__ import annotations

import ast
import importlib
import logging
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest


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


def _build_shim(rel_path: str, *method_names: str, global_ns: dict | None = None):
    methods = []
    for method_name in method_names:
        methods.append(textwrap.indent(_method_source(rel_path, method_name), "    "))
    code = "class AuditShim:\n" + ("\n\n".join(methods) or "    pass\n")
    namespace: dict = {}
    exec(code, global_ns or {}, namespace)
    return namespace["AuditShim"]


def _load_connectivity_controller(monkeypatch):
    fake_kde_mod = types.ModuleType("backend.kdeconnect")
    fake_kde_mod.KDEConnect = type("KDEConnect", (), {})
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", fake_kde_mod)
    sys.modules.pop("backend.connectivity_controller", None)
    return importlib.import_module("backend.connectivity_controller")


def test_sync_audio_route_async_coalesces_follow_up_request_and_merges_suspend_flags():
    release_first = threading.Event()
    calls = []

    class _AudioRoute:
        @staticmethod
        def sync(*, suspend_ui_global):
            calls.append(bool(suspend_ui_global))
            if len(calls) == 1:
                release_first.wait(1.0)

    shim_cls = _build_shim(
        "ui/window_runtime.py",
        "_ensure_runtime_async_state",
        "_sync_audio_route_async",
        global_ns={
            "audio_route": _AudioRoute,
            "threading": threading,
            "log": logging.getLogger("audit"),
        },
    )
    shim = shim_cls()
    shim._audio_route_sync_busy = False
    shim._audio_route_sync_pending = False
    shim._audio_route_sync_pending_suspend = False
    shim._runtime_async_lock = threading.Lock()

    shim._sync_audio_route_async(suspend_ui_global=True)
    shim._sync_audio_route_async(suspend_ui_global=False)
    shim._sync_audio_route_async(suspend_ui_global=True)

    release_first.set()
    deadline = time.time() + 1.0
    while len(calls) < 2 and time.time() < deadline:
        time.sleep(0.01)

    assert calls == [True, True]
    assert shim._audio_route_sync_busy is False
    assert shim._audio_route_sync_pending is False
    assert shim._audio_route_sync_pending_suspend is False


def test_sync_notification_snapshot_coalesces_second_refresh(monkeypatch):
    release_first = threading.Event()
    get_calls = []
    mirrored = []
    state_updates = []

    class _FakeSettings:
        @staticmethod
        def get(key, default=None):
            if key == "kde_integration_enabled":
                return True
            return default

    class _FakeState:
        @staticmethod
        def set(key, value):
            state_updates.append((key, value))

    class _FakeKDEConnect:
        def get_notifications(self):
            get_calls.append("get_notifications")
            if len(get_calls) == 1:
                release_first.wait(1.0)
            return [{"id": "n1"}]

    fake_mod = types.ModuleType("backend.kdeconnect")
    fake_mod.KDEConnect = _FakeKDEConnect
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", fake_mod)

    shim_cls = _build_shim(
        "ui/window_runtime.py",
        "_ensure_runtime_async_state",
        "_sync_notification_mirror_snapshot",
        global_ns={
            "settings": _FakeSettings,
            "sync_desktop_notifications": lambda rows: mirrored.append(list(rows or [])),
            "normalize_notifications": lambda rows: rows,
            "state": _FakeState,
            "threading": threading,
            "log": logging.getLogger("audit"),
        },
    )
    shim = shim_cls()
    shim._notif_sync_busy = False
    shim._notif_sync_pending = False
    shim._runtime_async_lock = threading.Lock()

    shim._sync_notification_mirror_snapshot()
    shim._sync_notification_mirror_snapshot()

    release_first.set()
    deadline = time.time() + 1.0
    while len(get_calls) < 2 and time.time() < deadline:
        time.sleep(0.01)

    assert get_calls == ["get_notifications", "get_notifications"]
    assert mirrored == [[{"id": "n1"}], [{"id": "n1"}]]
    assert state_updates == [
        ("notifications", [{"id": "n1"}]),
        ("notifications", [{"id": "n1"}]),
    ]
    assert shim._notif_sync_pending is False


def test_poll_phone_call_state_async_coalesces_trailing_poll():
    release_first = threading.Event()
    adb_calls = []
    emitted = []

    class _Plan:
        call_state = "idle"
        state_changed = False
        next_route_suspended = False
        should_synthesize_from_notifications = False
        sync_audio_suspend = False
        sync_audio_restore = False
        action = ""
        number = ""
        contact_name = ""

    class _FakeADB:
        @staticmethod
        def get_call_state_fast():
            adb_calls.append("poll")
            if len(adb_calls) == 1:
                release_first.wait(1.0)
            return "ringing"

    shim_cls = _build_shim(
        "ui/window_runtime.py",
        "_ensure_runtime_async_state",
        "_poll_phone_call_state_async",
        "_apply_polled_call_state",
        global_ns={
            "QTimer": type("QTimer", (), {"singleShot": staticmethod(lambda _delay, callback: callback())}),
            "plan_polled_call_state": lambda *_a, **_kw: _Plan(),
            "settings": types.SimpleNamespace(get=lambda *_a, **_kw: False),
            "state": types.SimpleNamespace(get=lambda *_a, **_kw: {}, set=lambda *_a, **_kw: None),
            "threading": threading,
            "time": time,
            "log": logging.getLogger("audit"),
        },
    )
    shim = shim_cls()
    shim._call_state_poll_busy = False
    shim._call_state_poll_pending = False
    shim._suspend_poll_until = 0.0
    shim._runtime_async_lock = threading.Lock()
    shim._adb = _FakeADB()
    shim._last_polled_call_state = "unknown"
    shim._call_state_route_suspended = False
    shim._call_controller = None
    shim._observe_polled_live_state = lambda *_a, **_kw: None
    shim._maybe_synthesize_call_from_notifications = lambda **_kw: None
    shim._mirror_stream_running = lambda: False
    shim._polled_ringing_edge_can_open_session = lambda **_kw: False
    shim._publish_call_snapshot = lambda *_a, **_kw: None
    shim._on_call_received = lambda *_a, **_kw: None
    shim._session_should_finalize_from_idle = lambda **_kw: False
    shim._cancel_poll_popup_fallback = lambda: None

    class _Signal:
        def emit(self, value):
            emitted.append(value)
            shim._apply_polled_call_state(value)

    shim._call_state_ready = _Signal()

    shim._poll_phone_call_state_async()
    shim._poll_phone_call_state_async()

    release_first.set()
    deadline = time.time() + 1.0
    while len(adb_calls) < 2 and time.time() < deadline:
        time.sleep(0.01)

    assert adb_calls == ["poll", "poll"]
    assert emitted == ["ringing", "ringing"]
    assert shim._call_state_poll_pending is False


def test_connectivity_same_operation_lock_rejects_reentry(monkeypatch):
    controller = _load_connectivity_controller(monkeypatch)
    controller.state.set("connectivity_ops_busy", {})
    first = controller._try_begin("wifi")
    second = controller._try_begin("wifi")
    try:
        assert first is not None
        assert second is None
    finally:
        controller._end("wifi", first)


def test_connectivity_cross_operation_locks_are_serialized(monkeypatch):
    controller = _load_connectivity_controller(monkeypatch)
    controller.state.set("connectivity_ops_busy", {})
    controller.state.set("connectivity_active_op", "")
    wifi_lock = controller._try_begin("wifi")
    bt_lock = controller._try_begin("bluetooth")
    try:
        assert wifi_lock is not None
        assert bt_lock is None
        busy = controller.state.get("connectivity_ops_busy", {}) or {}
        assert busy.get("wifi") is True
        assert busy.get("bluetooth") is not True
        assert controller.state.get("connectivity_active_op", "") == "wifi"
    finally:
        controller._end("wifi", wifi_lock)
        controller._end("bluetooth", bt_lock)
    assert controller.state.get("connectivity_active_op", "") == ""


def test_clipboard_controller_reads_cached_wayland_text_and_moves_subprocess_work_to_helper():
    method_src = _method_source("ui/runtime_controllers.py", "_read_current_text")
    helper_src = _method_source("ui/runtime_controllers.py", "_poll_wayland_clipboard_text")
    assert "subprocess.run(" not in method_src
    assert "self._schedule_wayland_clipboard_refresh()" in method_src
    assert "subprocess.run(" in helper_src


def test_dbus_signal_bridge_tracks_and_joins_worker_thread():
    start_src = _method_source("ui/window_support.py", "start")
    stop_src = _method_source("ui/window_support.py", "stop")

    assert "self._thread = threading.Thread(" in start_src
    assert "thread.join(" in stop_src


@pytest.mark.parametrize(
    ("method_name", "busy_flag"),
    [
        ("_sync_audio_route_async", "_audio_route_sync_busy"),
        ("_poll_phone_call_state_async", "_call_state_poll_busy"),
        ("_sync_notification_mirror_snapshot", "_notif_sync_busy"),
    ],
)
def test_window_runtime_busy_paths_return_early_when_already_busy(method_name, busy_flag):
    method_src = _method_source("ui/window_runtime.py", method_name)
    assert f"if self.{busy_flag}:" in method_src
    if method_name == "_sync_audio_route_async":
        assert "self._audio_route_sync_pending = True" in method_src
    elif method_name == "_poll_phone_call_state_async":
        assert "self._call_state_poll_pending = True" in method_src
    else:
        assert "self._notif_sync_pending = True" in method_src
