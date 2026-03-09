"""Contract tests for the documented runtime configuration surface."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.runtime_config as runtime_config
import backend.settings_store as settings


def _write_settings(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    settings._cache = None
    yield
    settings._cache = None


def test_documented_env_vars_match_settings_override_contract():
    assert runtime_config.documented_env_vars() == tuple(settings.ENV_OVERRIDES.values())


def test_runtime_config_prefers_settings_and_env(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    _write_settings(
        cfg,
        {
            "adb_target": "192.0.2.10:5555",
            "device_id": "device-from-settings",
            "device_name": "Device From Settings",
            "phone_tailscale_ip": "100.64.0.10",
            "nixos_tailscale_ip": "100.64.0.20",
            "syncthing_url": "http://127.0.0.1:8384",
            "syncthing_api_key": "settings-key",
        },
    )
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    monkeypatch.setenv("PHONEBRIDGE_DEVICE_NAME", "Device From Env")
    settings._cache = None

    assert runtime_config.adb_target() == "192.0.2.10:5555"
    assert runtime_config.device_id() == "device-from-settings"
    assert runtime_config.device_name() == "Device From Env"
    assert runtime_config.phone_tailscale_ip() == "100.64.0.10"
    assert runtime_config.host_tailscale_ip() == "100.64.0.20"
    assert runtime_config.syncthing_url() == "http://127.0.0.1:8384"
    assert runtime_config.syncthing_api_key() == "settings-key"


def test_shorten_home_path_uses_tilde(monkeypatch, tmp_path):
    home = tmp_path / "home" / "tester"
    fake_path = SimpleNamespace(home=lambda: home)
    monkeypatch.setattr(runtime_config, "Path", fake_path)
    assert runtime_config.shorten_home_path(str(home / "PhoneSync")) == "~/PhoneSync"
    assert runtime_config.shorten_home_path(str(tmp_path / "other")) == str(tmp_path / "other")
