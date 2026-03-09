"""Deterministic tests for KDE native notification popup suppression writer."""

from __future__ import annotations

import importlib
import stat
import sys
import types


for mod_name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

_dbus = sys.modules["dbus"]
_dbus.SessionBus = lambda: None
_dbus.Interface = lambda *a, **kw: None
_dbus_mainloop_glib = sys.modules["dbus.mainloop.glib"]
_dbus_mainloop_glib.DBusGMainLoop = lambda set_as_default=False: None
_gi_mod = sys.modules["gi"]
_gi_repo = sys.modules["gi.repository"]
_gi_mod.repository = _gi_repo
_gi_repo.GLib = types.SimpleNamespace(MainLoop=object)


def test_suppression_writes_all_event_sections(tmp_path, monkeypatch):
    import backend.kdeconnect as kc

    kc = importlib.reload(kc)

    monkeypatch.setenv("HOME", str(tmp_path))
    fake_root = tmp_path / "fake-kdeconnect"
    (fake_root / "bin").mkdir(parents=True, exist_ok=True)
    (fake_root / "share" / "knotifications6").mkdir(parents=True, exist_ok=True)

    fake_cli = fake_root / "bin" / "kdeconnect-cli"
    fake_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IXUSR)

    upstream = fake_root / "share" / "knotifications6" / "kdeconnect.notifyrc"
    upstream.write_text(
        "\n".join(
            [
                "[Event/notification]",
                "Action=Popup",
                "[Event/callReceived]",
                "Action=Popup",
                "[Event/customEvent]",
                "Action=Popup",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        kc.kde_notifications.shutil,
        "which",
        lambda name: str(fake_cli) if name == "kdeconnect-cli" else None,
    )
    monkeypatch.setattr(
        kc.kde_notifications.subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    changed = kc.KDEConnect.suppress_native_notification_popups(True)
    assert changed is True

    user_cfg = tmp_path / ".config" / "knotifications6" / "kdeconnect.notifyrc"
    text = user_cfg.read_text(encoding="utf-8")
    assert "[Event/notification]" in text
    assert "[Event/callReceived]" in text
    assert "[Event/customEvent]" in text
    assert "Action=None" in text
    assert "ShowInHistory=false" in text
    assert "Action=Popup" not in text
