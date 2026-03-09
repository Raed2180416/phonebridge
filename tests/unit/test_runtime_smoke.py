"""Small runtime smoke suite for core entrypoints."""

from __future__ import annotations

import importlib
import sys
import types

import pytest


for mod_name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

sys.modules["dbus"].SessionBus = lambda: None
sys.modules["dbus"].Interface = lambda *a, **kw: None
sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda set_as_default=False: None
sys.modules["gi"].repository = sys.modules["gi.repository"]
sys.modules["gi.repository"].GLib = types.SimpleNamespace(MainLoop=object)


def test_runtime_smoke_imports_core_entrypoints():
    for mod_name in (
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "ui",
        "ui.runtime_controllers",
        "ui.components",
        "ui.components.call_popup",
    ):
        existing = sys.modules.get(mod_name)
        if existing is not None and not getattr(existing, "__file__", None):
            sys.modules.pop(mod_name, None)
    modules = {
        "main": importlib.import_module("main"),
        "backend.autostart": importlib.import_module("backend.autostart"),
        "backend.health": importlib.import_module("backend.health"),
        "backend.notification_mirror": importlib.import_module("backend.notification_mirror"),
        "backend.connectivity_snapshot": importlib.import_module("backend.connectivity_snapshot"),
        "backend.call_routing": importlib.import_module("backend.call_routing"),
        "backend.runtime_config": importlib.import_module("backend.runtime_config"),
    }
    try:
        modules["ui.runtime_controllers"] = importlib.import_module("ui.runtime_controllers")
        modules["ui.components.call_popup"] = importlib.import_module("ui.components.call_popup")
    except ImportError as exc:
        pytest.skip(f"PyQt runtime unavailable for smoke import: {exc}")

    assert hasattr(modules["main"], "_ensure_runtime_or_reexec")
    assert hasattr(modules["backend.autostart"], "publish_runtime")
    assert hasattr(modules["backend.health"], "probe_all_services")
    assert hasattr(modules["backend.notification_mirror"], "sync_desktop_notifications")
    assert hasattr(modules["backend.connectivity_snapshot"], "collect_snapshot")
    assert hasattr(modules["backend.call_routing"], "normalize_call_event")
    assert hasattr(modules["backend.runtime_config"], "documented_env_vars")
    assert hasattr(modules["ui.runtime_controllers"], "CallController")
    assert hasattr(modules["ui.components.call_popup"], "CallPopup")
