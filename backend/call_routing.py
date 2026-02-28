"""Pure call routing helpers (no Qt/DBus dependencies).

Used both by runtime (ui/window.py) and optional deterministic tests.
"""

from __future__ import annotations

import re
import time
from typing import Any


def normalize_call_event(event: str) -> str:
    e = (event or "").strip().lower().replace("-", "_")
    if "missed" in e:
        return "missed_call"
    if e in {"ended", "end", "hangup", "disconnected", "idle", "terminated", "declined", "rejected"}:
        return "ended"
    if e in {"ringing", "callreceived", "incoming", "incoming_call"}:
        return "ringing"
    if e in {"talking", "answered", "in_call", "ongoing", "active", "callstarted"}:
        return "talking"
    return e or "ringing"


def outbound_origin_active(origin: dict[str, Any] | None, *, now_ms: int | None = None) -> bool:
    row = origin or {}
    if row.get("source") != "calls_page":
        return False
    if not bool(row.get("active")):
        return False
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    ts_ms = int(row.get("ts_ms", 0) or 0)
    return (now_ms - ts_ms) < 75_000


def should_suppress_popup(normalized_event: str, origin: dict[str, Any] | None, *, now_ms: int | None = None) -> bool:
    if normalized_event not in {"ringing", "talking"}:
        return False
    return outbound_origin_active(origin, now_ms=now_ms)

