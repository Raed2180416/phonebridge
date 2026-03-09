"""Slate-only theme normalization tests."""

from __future__ import annotations

import json

import backend.settings_store as settings


def test_load_normalizes_saved_theme_to_slate(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"theme_name": "night"}), encoding="utf-8")

    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    settings._cache = None

    loaded = settings.load()

    assert loaded["theme_name"] == "slate"


def test_set_theme_name_persists_slate_only(monkeypatch, tmp_path):
    cfg = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", str(cfg))
    settings._cache = None
    settings.load()

    settings.set("theme_name", "mist")

    settings._cache = None
    loaded = settings.load()

    assert loaded["theme_name"] == "slate"
    payload = json.loads(cfg.read_text(encoding="utf-8"))
    assert payload["theme_name"] == "slate"
