"""Deterministic tests for notification normalization with phone-active truth."""

from __future__ import annotations

import importlib
import json


def _fresh_module(tmp_path):
    mod = importlib.import_module("backend.notifications_state")
    mod = importlib.reload(mod)
    mod._STATE_PATH = str(tmp_path / "notifications_state.json")
    mod._CACHE = None
    mod._SESSION_HIDDEN_UNTIL_MS_BY_ID.clear()
    mod._SESSION_HIDDEN_UNTIL_MS_BY_CALL_KEY.clear()
    return mod


def test_normalize_ignores_legacy_dismissed_tombstones(tmp_path):
    ns = _fresh_module(tmp_path)
    legacy = {
        "schema_version": 1,
        "dismissed_ids": ["a"],
        "dismissed_fingerprint_by_id": {"a": "whatever"},
    }
    (tmp_path / "notifications_state.json").write_text(json.dumps(legacy), encoding="utf-8")
    rows = ns.normalize_notifications(
        [
            {"id": "a", "app": "Signal", "title": "Hi", "text": "one", "time_ms": 1000},
        ]
    )
    assert [r["id"] for r in rows] == ["a"]


def test_preserves_kde_order_when_time_missing(tmp_path):
    ns = _fresh_module(tmp_path)
    rows = ns.normalize_notifications(
        [
            {"id": "c", "app": "Signal", "title": "C"},
            {"id": "a", "app": "Signal", "title": "A"},
            {"id": "b", "app": "Signal", "title": "B"},
        ]
    )
    assert [r["id"] for r in rows] == ["c", "a", "b"]


def test_uses_time_ms_order_when_present(tmp_path):
    ns = _fresh_module(tmp_path)
    rows = ns.normalize_notifications(
        [
            {"id": "a", "app": "Signal", "title": "A", "time_ms": 1000},
            {"id": "b", "app": "Signal", "title": "B"},
            {"id": "c", "app": "Signal", "title": "C", "time_ms": 3000},
            {"id": "d", "app": "Signal", "title": "D"},
        ]
    )
    assert [r["id"] for r in rows] == ["c", "a", "b", "d"]


def test_meta_filter_is_explicit_and_avoids_false_positive_keyword_drops(tmp_path):
    ns = _fresh_module(tmp_path)
    rows = ns.normalize_notifications(
        [
            {"id": "meta", "app": "KDE Connect", "title": "Pairing request", "text": "Tap to pair"},
            {"id": "sig1", "app": "Signal", "title": "Battery connected", "text": "This is app content"},
            {"id": "k1", "app": "KDE Connect", "title": "Alice", "text": "normal content"},
        ]
    )
    assert [r["id"] for r in rows] == ["sig1", "k1"]


def test_record_dismissed_is_session_scoped_and_short_lived(tmp_path, monkeypatch):
    ns = _fresh_module(tmp_path)
    current = {"value": 100.0}

    def _fake_time():
        return current["value"]

    monkeypatch.setattr(ns.time, "time", _fake_time)
    rows = [{"id": "x", "app": "Signal", "title": "A", "text": "B"}]
    assert [r["id"] for r in ns.normalize_notifications(rows)] == ["x"]
    ns.record_dismissed("x")
    assert ns.normalize_notifications(rows) == []
    current["value"] += 3.0
    assert [r["id"] for r in ns.normalize_notifications(rows)] == ["x"]


def test_normalize_keeps_internal_id_and_actions_support_flag(tmp_path):
    ns = _fresh_module(tmp_path)
    rows = ns.normalize_notifications(
        [
            {
                "id": "99",
                "app": "Signal",
                "title": "t",
                "text": "x",
                "internal_id": "0|com.signal|abc",
                "actions_supported": False,
            }
        ]
    )
    assert rows[0]["internal_id"] == "0|com.signal|abc"
    assert rows[0]["actions_supported"] is False


def test_normalize_dedupes_phone_call_rows_by_internal_id(tmp_path):
    ns = _fresh_module(tmp_path)
    rows = ns.normalize_notifications(
        [
            {
                "id": "24",
                "app": "Phone",
                "title": "Mom",
                "text": "Incoming call",
                "internal_id": "call|mom",
                "time_ms": 1000,
            },
            {
                "id": "25",
                "app": "Phone",
                "title": "Mom",
                "text": "Incoming call",
                "internal_id": "call|mom",
                "time_ms": 3000,
            },
        ]
    )
    assert [row["id"] for row in rows] == ["25"]


def test_normalize_dedupes_phone_call_rows_by_stable_content_when_internal_id_missing(tmp_path):
    ns = _fresh_module(tmp_path)
    rows = ns.normalize_notifications(
        [
            {
                "id": "26",
                "app": "Phone",
                "title": "Mom",
                "text": "Incoming call",
                "time_ms": 1000,
            },
            {
                "id": "27",
                "app": "Phone",
                "title": "Mom",
                "text": "Incoming call",
                "time_ms": 2000,
            },
        ]
    )
    assert [row["id"] for row in rows] == ["27"]


def test_hidden_phone_call_key_filters_matching_rows_for_session_ttl(tmp_path, monkeypatch):
    ns = _fresh_module(tmp_path)
    current = {"value": 100.0}
    monkeypatch.setattr(ns.time, "time", lambda: current["value"])

    row = {
        "id": "24",
        "app": "Phone",
        "title": "Mom",
        "text": "Incoming call",
        "internal_id": "call|mom",
    }
    call_key = ns.phone_call_notification_key(row)
    ns.record_hidden_call_keys([call_key], ttl_ms=15_000)
    assert ns.normalize_notifications([row]) == []

    current["value"] += 16.0
    rows = ns.normalize_notifications([row])
    assert [item["id"] for item in rows] == ["24"]
