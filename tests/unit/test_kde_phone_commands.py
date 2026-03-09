"""Deterministic tests for KDE phone command pack."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import types
import json
import re

import scripts.kde_remote_actions as actions


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install_kde_phone_commands.sh"


def _make_fake_cmd(path: Path, name: str):
    target = path / name
    target.write_text("#!/usr/bin/env bash\necho \"$0 $*\" >> \"$TEST_LOG\"\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)


def _run_installer(tmp_path: Path, *args: str):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    _make_fake_cmd(fake_bin, "systemctl")
    _make_fake_cmd(fake_bin, "kdeconnect-cli")

    device_id = "0123456789abcdef0123456789abcdef"
    device_dir = tmp_path / ".config" / "kdeconnect" / device_id
    device_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["TEST_LOG"] = str(tmp_path / "cmd.log")

    cmd = [str(INSTALLER), "--device-id", device_id, "--project-root", str(REPO_ROOT), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)


def test_installer_writes_plugin_flag(tmp_path):
    res = _run_installer(tmp_path)
    assert res.returncode == 0
    cfg = tmp_path / ".config" / "kdeconnect" / "0123456789abcdef0123456789abcdef" / "config"
    text = cfg.read_text(encoding="utf-8")
    assert "[Plugins]" in text
    assert "kdeconnect_runcommandEnabled = true" in text


def test_installer_writes_runcommand_config_with_expected_ids_and_names(tmp_path):
    res = _run_installer(tmp_path)
    assert res.returncode == 0
    cfg = tmp_path / ".config" / "kdeconnect" / "0123456789abcdef0123456789abcdef" / "kdeconnect_runcommand" / "config"
    text = cfg.read_text(encoding="utf-8")
    m = re.search(r'commands\s*=\s*\"@ByteArray\((.+)\)\"', text)
    assert m is not None
    payload = json.loads(m.group(1).replace('\\"', '"'))
    assert payload["pb_lock_laptop"]["name"] == "Lock Laptop"
    assert payload["pb_shutdown_laptop"]["name"] == "Shutdown Laptop"
    assert payload["pb_logout_laptop"]["name"] == "Logout Laptop"
    assert payload["pb_audio_to_phone"]["name"] == "Audio to Phone"
    assert payload["pb_audio_to_pc"]["name"] == "Audio to PC"


def test_installer_writes_absolute_command_paths(tmp_path):
    res = _run_installer(tmp_path)
    assert res.returncode == 0
    cfg = tmp_path / ".config" / "kdeconnect" / "0123456789abcdef0123456789abcdef" / "kdeconnect_runcommand" / "config"
    text = cfg.read_text(encoding="utf-8")
    m = re.search(r'commands\s*=\s*\"@ByteArray\((.+)\)\"', text)
    assert m is not None
    payload = json.loads(m.group(1).replace('\\"', '"'))
    expected_prefix = f"python3 {REPO_ROOT / 'scripts' / 'kde_remote_actions.py'}"
    for row in payload.values():
        assert str(row["command"]).startswith(expected_prefix)


def test_remove_only_deletes_runcommand_config(tmp_path):
    res = _run_installer(tmp_path)
    assert res.returncode == 0

    cfg = tmp_path / ".config" / "kdeconnect" / "0123456789abcdef0123456789abcdef" / "config"
    run_cfg = tmp_path / ".config" / "kdeconnect" / "0123456789abcdef0123456789abcdef" / "kdeconnect_runcommand" / "config"
    assert cfg.exists()
    assert run_cfg.exists()

    res2 = _run_installer(tmp_path, "--remove")
    assert res2.returncode == 0
    assert cfg.exists()
    assert not run_cfg.exists()


def test_lock_laptop_picks_first_available_command(monkeypatch):
    called = []

    def fake_run(cmd, timeout=8.0):
        called.append(cmd)
        if cmd[:3] == ["loginctl", "lock-session", "ABC"]:
            return actions.CommandResult(True, "", "", 0)
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(actions, "run_cmd", fake_run)
    monkeypatch.setenv("XDG_SESSION_ID", "ABC")

    assert actions.main(["lock-laptop"]) == 0
    assert called == [["loginctl", "lock-session", "ABC"]]


def test_audio_to_pc_calls_set_enabled_true_and_sync(monkeypatch):
    fake_mod = types.SimpleNamespace()
    state = {"enabled": None, "sync_calls": 0}

    def set_enabled(val):
        state["enabled"] = bool(val)

    def sync():
        state["sync_calls"] += 1
        return True

    fake_mod.set_enabled = set_enabled
    fake_mod.sync = sync

    monkeypatch.setattr(actions, "ensure_phonebridge_background", lambda: True)
    monkeypatch.setattr(actions, "_ensure_repo_import_path", lambda: None)
    monkeypatch.setattr(actions.importlib, "import_module", lambda name: fake_mod)

    assert actions.main(["audio-to-pc"]) == 0
    assert state["enabled"] is True
    assert state["sync_calls"] == 1


def test_audio_to_phone_calls_set_enabled_false_and_sync(monkeypatch):
    fake_mod = types.SimpleNamespace()
    state = {"enabled": None, "sync_calls": 0}

    def set_enabled(val):
        state["enabled"] = bool(val)

    def sync():
        state["sync_calls"] += 1
        return True

    fake_mod.set_enabled = set_enabled
    fake_mod.sync = sync

    monkeypatch.setattr(actions, "ensure_phonebridge_background", lambda: True)
    monkeypatch.setattr(actions, "_ensure_repo_import_path", lambda: None)
    monkeypatch.setattr(actions.importlib, "import_module", lambda name: fake_mod)

    assert actions.main(["audio-to-phone"]) == 0
    assert state["enabled"] is False
    assert state["sync_calls"] == 1


def test_action_failure_returns_nonzero(monkeypatch):
    monkeypatch.delenv("XDG_SESSION_ID", raising=False)

    def fail_all(cmd, timeout=8.0):
        return actions.CommandResult(False, "", "nope", 1)

    monkeypatch.setattr(actions, "run_cmd", fail_all)
    assert actions.main(["logout-laptop"]) != 0
