"""Deterministic tests for startup integration consent gating and Hypr binding scope."""

from __future__ import annotations

import sys
import importlib as _importlib
from pathlib import Path

# Force fresh imports — prior tests may have stubbed these modules
sys.modules.pop("backend.system_integration", None)
sys.modules.pop("backend.autostart", None)
import backend.system_integration as si  # noqa: E402
_importlib.reload(si)


def test_ensure_system_integration_skips_mutations_when_opt_out(monkeypatch):
    calls = {"icon": 0, "desktop": 0, "hypr": 0, "autostart": 0}

    def _get(key, default=None):
        mapping = {
            "integration_manage_icon": False,
            "integration_manage_desktop_entry": False,
            "integration_manage_hypr_bind": False,
            "integration_manage_autostart": False,
        }
        return mapping.get(key, default)

    monkeypatch.setattr(si.settings, "get", _get)
    monkeypatch.setattr(si, "ensure_icon", lambda: calls.__setitem__("icon", calls["icon"] + 1) or (True, "ok"))
    monkeypatch.setattr(si, "ensure_desktop_entry", lambda _root: calls.__setitem__("desktop", calls["desktop"] + 1) or (True, "ok"))
    monkeypatch.setattr(si, "ensure_hyprland_toggle_binding", lambda _root: calls.__setitem__("hypr", calls["hypr"] + 1) or (True, "ok"))
    monkeypatch.setattr(si.autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(si.autostart, "set_enabled", lambda _enabled: calls.__setitem__("autostart", calls["autostart"] + 1) or (True, "ok"))

    si.ensure_system_integration("/tmp/phonebridge")

    assert calls == {"icon": 0, "desktop": 0, "hypr": 0, "autostart": 0}


def test_ensure_system_integration_runs_mutations_when_opt_in(monkeypatch):
    calls = {"icon": 0, "desktop": 0, "hypr": 0, "autostart": 0}

    def _get(key, default=None):
        mapping = {
            "integration_manage_icon": True,
            "integration_manage_desktop_entry": True,
            "integration_manage_hypr_bind": True,
            "integration_manage_autostart": True,
        }
        return mapping.get(key, default)

    monkeypatch.setattr(si.settings, "get", _get)
    monkeypatch.setattr(si, "ensure_icon", lambda: calls.__setitem__("icon", calls["icon"] + 1) or (True, "ok"))
    monkeypatch.setattr(si, "ensure_desktop_entry", lambda _root: calls.__setitem__("desktop", calls["desktop"] + 1) or (True, "ok"))
    monkeypatch.setattr(si, "ensure_hyprland_toggle_binding", lambda _root: calls.__setitem__("hypr", calls["hypr"] + 1) or (True, "ok"))
    monkeypatch.setattr(si.autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(si.autostart, "set_enabled", lambda _enabled: calls.__setitem__("autostart", calls["autostart"] + 1) or (True, "ok"))

    si.ensure_system_integration("/tmp/phonebridge")

    assert calls == {"icon": 1, "desktop": 1, "hypr": 1, "autostart": 1}


def test_hypr_managed_file_contains_only_phonebridge_bind(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    hypr_dir = tmp_path / ".config" / "hypr"
    hypr_dir.mkdir(parents=True, exist_ok=True)
    main_conf = hypr_dir / "hyprland.conf"
    main_conf.write_text("$mod = SUPER\n", encoding="utf-8")

    monkeypatch.setattr(si, "_run", lambda cmd: (True, "ok"))

    ok, info = si.ensure_hyprland_toggle_binding(Path("/proj"))
    assert ok is True
    assert "phonebridge.conf" in info

    bind_conf = hypr_dir / "phonebridge.conf"
    content = bind_conf.read_text(encoding="utf-8")
    assert "bind = SUPER, P, exec," in content
    assert "SUPER, F" not in content

    merged_main = main_conf.read_text(encoding="utf-8")
    assert si.HYPR_INCLUDE_LINE in merged_main
