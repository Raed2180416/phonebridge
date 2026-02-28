"""Deterministic tests for outbound-call popup suppression logic.

This intentionally tests pure routing helpers only (no Qt/DBus deps).
"""

from __future__ import annotations

import time

from backend.call_routing import normalize_call_event, outbound_origin_active, should_suppress_popup


def test_normalize_call_event():
    assert normalize_call_event("incoming_call") == "ringing"
    assert normalize_call_event("callReceived") == "ringing"
    assert normalize_call_event("answered") == "talking"
    assert normalize_call_event("missed") == "missed_call"
    assert normalize_call_event("hangup") == "ended"


def test_outbound_origin_active_true_with_recent_calls_page_origin():
    now_ms = int(time.time() * 1000)
    origin = {"source": "calls_page", "active": True, "ts_ms": now_ms - 1000}
    assert outbound_origin_active(origin, now_ms=now_ms) is True


def test_outbound_origin_active_false_when_expired_or_inactive():
    now_ms = int(time.time() * 1000)
    assert outbound_origin_active({"source": "calls_page", "active": False, "ts_ms": now_ms}, now_ms=now_ms) is False
    assert outbound_origin_active({"source": "calls_page", "active": True, "ts_ms": now_ms - 80_000}, now_ms=now_ms) is False
    assert outbound_origin_active({"source": "other", "active": True, "ts_ms": now_ms}, now_ms=now_ms) is False


def test_should_suppress_popup_only_for_ringing_or_talking():
    now_ms = int(time.time() * 1000)
    origin = {"source": "calls_page", "active": True, "ts_ms": now_ms}

    assert should_suppress_popup("ringing", origin, now_ms=now_ms) is True
    assert should_suppress_popup("talking", origin, now_ms=now_ms) is True
    assert should_suppress_popup("ended", origin, now_ms=now_ms) is False
    assert should_suppress_popup("missed_call", origin, now_ms=now_ms) is False

