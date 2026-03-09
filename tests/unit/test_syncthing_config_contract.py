"""Deterministic contract tests for Syncthing config resolution and startup status mapping."""

from __future__ import annotations

import json
from pathlib import Path

import backend.settings_store as settings
import backend.syncthing as syncthing


def _write_settings(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_syncthing_uses_settings_file_values(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    _write_settings(
        cfg,
        {
            "syncthing_url": "http://127.0.0.1:8384",
            "syncthing_api_key": "file-key",
        },
    )
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    settings._cache = None

    st = syncthing.Syncthing()
    assert st._url == "http://127.0.0.1:8384"
    assert st._api_key == "file-key"


def test_syncthing_env_overrides_settings(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    _write_settings(
        cfg,
        {
            "syncthing_url": "http://127.0.0.1:8384",
            "syncthing_api_key": "file-key",
        },
    )
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    monkeypatch.setenv("PHONEBRIDGE_SYNCTHING_URL", "http://localhost:8385")
    monkeypatch.setenv("PHONEBRIDGE_SYNCTHING_API_KEY", "env-key")
    settings._cache = None

    st = syncthing.Syncthing()
    assert st._url == "http://localhost:8385"
    assert st._api_key == "env-key"


def test_syncthing_ping_status_without_key_is_deterministic(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    _write_settings(cfg, {"syncthing_api_key": ""})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    monkeypatch.delenv("PHONEBRIDGE_SYNCTHING_API_KEY", raising=False)
    settings._cache = None

    st = syncthing.Syncthing()
    ok, code, reason = st.ping_status()
    assert ok is False
    assert code is None
    assert reason == "missing_api_key"
