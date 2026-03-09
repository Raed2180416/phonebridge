"""Audit coverage for async runtime and connectivity risk patterns."""

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


def test_sync_audio_route_async_drops_follow_up_request_while_busy():
    started = threading.Event()
    release = threading.Event()
    calls = []

    class _AudioRoute:
        @staticmethod
        def sync(*, suspend_ui_global):
            calls.append(bool(suspend_ui_global))
            started.set()
            release.wait(1.0)

    shim_cls = _build_shim(
        "ui/window_runtime.py",
        "_sync_audio_route_async",
        global_ns={
            "audio_route": _AudioRoute,
            "threading": threading,
            "log": logging.getLogger("audit"),
        },
    )
    shim = shim_cls()
    shim._audio_route_sync_busy = False

    shim._sync_audio_route_async(suspend_ui_global=True)
    assert started.wait(1.0)
    shim._sync_audio_route_async(suspend_ui_global=False)

    release.set()
    deadline = time.time() + 1.0
    while shim._audio_route_sync_busy and time.time() < deadline:
        time.sleep(0.01)

    assert calls == [True]
    assert shim._audio_route_sync_busy is False


def test_sync_notification_snapshot_drops_second_refresh_while_busy(monkeypatch):
    started = threading.Event()
    release = threading.Event()
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
            started.set()
            release.wait(1.0)
            return [{"id": "n1"}]

    fake_mod = types.ModuleType("backend.kdeconnect")
    fake_mod.KDEConnect = _FakeKDEConnect
    monkeypatch.setitem(sys.modules, "backend.kdeconnect", fake_mod)

    shim_cls = _build_shim(
        "ui/window_runtime.py",
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

    shim._sync_notification_mirror_snapshot()
    assert started.wait(1.0)
    shim._sync_notification_mirror_snapshot()

    release.set()
    deadline = time.time() + 1.0
    while shim._notif_sync_busy and time.time() < deadline:
        time.sleep(0.01)

    assert get_calls == ["get_notifications"]
    assert mirrored == [[{"id": "n1"}]]
    assert state_updates == [("notifications", [{"id": "n1"}])]


def test_poll_phone_call_state_async_drops_second_poll_while_busy():
    started = threading.Event()
    release = threading.Event()
    adb_calls = []
    emitted = []

    class _FakeADB:
        @staticmethod
        def get_call_state_fast():
            adb_calls.append("poll")
            started.set()
            release.wait(1.0)
            return "ringing"

    class _Signal:
        @staticmethod
        def emit(value):
            emitted.append(value)

    shim_cls = _build_shim(
        "ui/window_runtime.py",
        "_poll_phone_call_state_async",
        global_ns={
            "threading": threading,
            "time": time,
            "log": logging.getLogger("audit"),
        },
    )
    shim = shim_cls()
    shim._call_state_poll_busy = False
    shim._suspend_poll_until = 0.0
    shim._adb = _FakeADB()
    shim._call_state_ready = _Signal()

    shim._poll_phone_call_state_async()
    assert started.wait(1.0)
    shim._poll_phone_call_state_async()

    release.set()
    deadline = time.time() + 1.0
    while shim._call_state_poll_busy and time.time() < deadline:
        time.sleep(0.01)

    assert adb_calls == ["poll"]
    assert emitted == ["ringing"]


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


def test_connectivity_cross_operation_locks_allow_overlap(monkeypatch):
    controller = _load_connectivity_controller(monkeypatch)
    controller.state.set("connectivity_ops_busy", {})
    wifi_lock = controller._try_begin("wifi")
    bt_lock = controller._try_begin("bluetooth")
    try:
        assert wifi_lock is not None
        assert bt_lock is not None
        busy = controller.state.get("connectivity_ops_busy", {}) or {}
        assert busy.get("wifi") is True
        assert busy.get("bluetooth") is True
    finally:
        controller._end("wifi", wifi_lock)
        controller._end("bluetooth", bt_lock)


def test_clipboard_controller_source_uses_blocking_subprocess_polling():
    method_src = _method_source("ui/runtime_controllers.py", "_read_wayland_clipboard_text")
    assert "subprocess.run(" in method_src
    assert "timeout=0.5" in method_src


def test_dbus_signal_bridge_start_and_stop_do_not_track_or_join_worker_thread():
    start_src = _method_source("ui/window_support.py", "start")
    stop_src = _method_source("ui/window_support.py", "stop")

    assert "thread = threading.Thread(" in start_src
    assert "self._thread" not in start_src
    assert ".join(" not in stop_src


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
    assert any("return" in line for line in method_src.splitlines()[1:4])
