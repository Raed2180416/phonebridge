"""Deterministic tests for integration consent migration behavior."""

from __future__ import annotations

import json

import backend.settings_store as settings


def test_fresh_defaults_keep_integration_mutations_opt_out(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    settings._cache = None

    loaded = settings.load()

    assert loaded["integration_manage_icon"] is False
    assert loaded["integration_manage_desktop_entry"] is False
    assert loaded["integration_manage_hypr_bind"] is False
    assert loaded["integration_manage_autostart"] is False


def test_existing_settings_migrate_missing_consent_from_legacy_artifacts(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"device_name": "Phone"}), encoding="utf-8")

    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    monkeypatch.setattr(
        settings,
        "_infer_legacy_integration_consent",
        lambda: {
            "integration_manage_icon": True,
            "integration_manage_desktop_entry": True,
            "integration_manage_hypr_bind": False,
            "integration_manage_autostart": True,
        },
    )
    settings._cache = None

    loaded = settings.load()

    assert loaded["integration_manage_icon"] is True
    assert loaded["integration_manage_desktop_entry"] is True
    assert loaded["integration_manage_hypr_bind"] is False
    assert loaded["integration_manage_autostart"] is True


def test_existing_explicit_consent_is_not_overwritten(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "integration_manage_icon": False,
                "integration_manage_desktop_entry": True,
                "integration_manage_hypr_bind": False,
                "integration_manage_autostart": False,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    monkeypatch.setattr(
        settings,
        "_infer_legacy_integration_consent",
        lambda: {
            "integration_manage_icon": True,
            "integration_manage_desktop_entry": True,
            "integration_manage_hypr_bind": True,
            "integration_manage_autostart": True,
        },
    )
    settings._cache = None

    loaded = settings.load()

    assert loaded["integration_manage_icon"] is False
    assert loaded["integration_manage_desktop_entry"] is True
    assert loaded["integration_manage_hypr_bind"] is False
    assert loaded["integration_manage_autostart"] is False
