"""Deterministic tests for dependency preflight module (PB-010)."""

from __future__ import annotations

import backend.preflight as preflight


def test_preflight_returns_structured_results(monkeypatch):
    # Simulate all binaries missing
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: None)
    preflight._cache = None
    results = preflight.check_all()
    assert isinstance(results, dict)
    for feature, info in results.items():
        assert "ok" in info
        assert "found" in info
        assert "candidates" in info
        assert "description" in info
        assert "fallback" in info
        assert info["ok"] is False
        assert info["found"] is None


def test_preflight_has_returns_false_when_all_missing(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: None)
    preflight._cache = None
    assert preflight.has("mirror") is False
    assert preflight.has("adb") is False


def test_preflight_has_returns_true_when_candidate_present(monkeypatch):
    def _fake_which(cmd):
        return f"/usr/bin/{cmd}" if cmd == "scrcpy" else None

    monkeypatch.setattr(preflight.shutil, "which", _fake_which)
    preflight._cache = None
    assert preflight.has("mirror") is True


def test_preflight_missing_text_empty_when_available(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    preflight._cache = None
    assert preflight.missing_text("adb") == ""


def test_preflight_missing_text_nonempty_when_missing(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: None)
    preflight._cache = None
    text = preflight.missing_text("adb")
    assert "adb" in text.lower()
    assert len(text) > 0


def test_preflight_summary_lines_empty_when_all_present(monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    preflight._cache = None
    assert preflight.summary_lines() == []


def test_preflight_summary_lines_nonempty_when_some_missing(monkeypatch):
    def _fake_which(cmd):
        # Only adb is missing
        return None if cmd == "adb" else f"/usr/bin/{cmd}"

    monkeypatch.setattr(preflight.shutil, "which", _fake_which)
    preflight._cache = None
    lines = preflight.summary_lines()
    assert any("adb" in line.lower() for line in lines)


def test_preflight_unknown_feature_returns_empty_string():
    text = preflight.missing_text("nonexistent_feature_xyz")
    assert "nonexistent_feature_xyz" in text


def test_preflight_cache_is_reused(monkeypatch):
    call_count = {"n": 0}
    real_check = preflight.check_all

    def _counting_check():
        call_count["n"] += 1
        return real_check()

    monkeypatch.setattr(preflight, "check_all", _counting_check)
    preflight._cache = None
    preflight.get()
    preflight.get()
    preflight.get()
    assert call_count["n"] == 1
