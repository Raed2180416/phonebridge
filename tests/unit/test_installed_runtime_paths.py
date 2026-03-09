"""Tests for installed launcher/runtime path consistency."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import backend.autostart as autostart
import backend.system_integration as system_integration


def test_desktop_entry_uses_preferred_runtime_launcher(tmp_path, monkeypatch):
    runtime_base = tmp_path / "runtime"
    current = runtime_base / "current"
    current.mkdir(parents=True, exist_ok=True)
    launcher = current / "run-venv-runtime.sh"
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "run-venv-nix.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_runtime_base_path", lambda: runtime_base)

    text = system_integration.desktop_entry_contents(project)
    assert f"Exec={launcher}" in text
    assert f"TryExec={launcher}" in text
    assert "run-venv-nix.sh" not in text


def test_toggle_script_prefers_installed_runtime_launcher(tmp_path):
    home = tmp_path / "home"
    runtime = home / ".local" / "share" / "phonebridge" / "runtime" / "current"
    runtime.mkdir(parents=True, exist_ok=True)
    launcher = runtime / "run-venv-runtime.sh"
    launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    launcher.chmod(0o755)

    script = Path("scripts/phonebridge-toggle.sh").resolve()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PHONEBRIDGE_TOGGLE_DRY_RUN"] = "1"
    env["PHONEBRIDGE_SKIP_SOCKET"] = "1"

    proc = subprocess.run([str(script)], capture_output=True, text=True, check=False, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"{launcher} --toggle"


def test_hypr_binding_prefers_installed_runtime_toggle_script(tmp_path, monkeypatch):
    home = tmp_path / "home"
    hypr_dir = home / ".config" / "hypr"
    hypr_dir.mkdir(parents=True, exist_ok=True)
    (hypr_dir / "hyprland.conf").write_text("$mod = SUPER\n", encoding="utf-8")

    runtime_base = home / ".local" / "share" / "phonebridge" / "runtime"
    current = runtime_base / "current"
    toggle_script = current / "scripts" / "phonebridge-toggle.sh"
    toggle_script.parent.mkdir(parents=True, exist_ok=True)
    toggle_script.write_text("#!/bin/sh\n", encoding="utf-8")
    toggle_script.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(system_integration, "_run", lambda _cmd: (True, "ok"))
    monkeypatch.setattr(system_integration.hyprland, "reload_config", lambda: None)
    monkeypatch.setattr(system_integration, "ensure_hyprland_call_popup_rules", lambda: (True, "ok"))
    monkeypatch.setattr(autostart, "_runtime_base_path", lambda: runtime_base)

    ok, path = system_integration.ensure_hyprland_toggle_binding(tmp_path / "project")

    assert ok is True
    assert path.endswith("phonebridge.conf")
    bind_conf = (hypr_dir / "phonebridge.conf").read_text(encoding="utf-8")
    assert str(toggle_script) in bind_conf


def test_toggle_script_runs_without_bash_on_path(tmp_path):
    home = tmp_path / "home"
    runtime = home / ".local" / "share" / "phonebridge" / "runtime" / "current"
    runtime.mkdir(parents=True, exist_ok=True)
    launcher = runtime / "run-venv-runtime.sh"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)

    script = Path("scripts/phonebridge-toggle.sh").resolve()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = "/tmp/phonebridge-no-bash"
    env["PHONEBRIDGE_TOGGLE_DRY_RUN"] = "1"
    env["PHONEBRIDGE_SKIP_SOCKET"] = "1"

    proc = subprocess.run([str(script)], capture_output=True, text=True, check=False, env=env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"{launcher} --toggle"


def test_settings_legacy_desktop_detection_accepts_runtime_launcher(tmp_path):
    desktop = tmp_path / "phonebridge.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Name=PhoneBridge\n"
        "Exec=/home/test/.local/share/phonebridge/runtime/current/run-venv-runtime.sh\n",
        encoding="utf-8",
    )

    from backend import settings_store

    assert settings_store._looks_like_phonebridge_desktop_entry(str(desktop)) is True
