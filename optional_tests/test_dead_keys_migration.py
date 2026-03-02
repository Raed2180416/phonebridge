"""Deterministic tests for dead settings key migration (PB-007)."""

from __future__ import annotations

import json

import backend.settings_store as settings


def test_dead_keys_not_present_in_fresh_defaults(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    settings._cache = None

    loaded = settings.load()

    assert "theme_variant" not in loaded
    assert "surface_alpha_mode" not in loaded


def test_dead_keys_stripped_from_existing_settings_file(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "device_name": "Phone",
                "theme_variant": "minimal_glass",
                "surface_alpha_mode": "auto_fallback",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    settings._cache = None

    loaded = settings.load()

    assert "theme_variant" not in loaded
    assert "surface_alpha_mode" not in loaded
    assert loaded["device_name"] == "Phone"


def test_dead_keys_absent_from_defaults_constant():
    assert "theme_variant" not in settings.DEFAULTS
    assert "surface_alpha_mode" not in settings.DEFAULTS


def test_dead_keys_listed_in_dead_keys_set():
    assert "theme_variant" in settings._DEAD_KEYS
    assert "surface_alpha_mode" in settings._DEAD_KEYS
