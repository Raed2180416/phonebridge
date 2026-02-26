"""Clipboard timeline normalization helpers."""
from __future__ import annotations

import time


def sanitize_clipboard_history(rows, limit=200):
    """Return a normalized list of {text, ts, source} rows."""
    out = []
    prev_text = None
    now = int(time.time())
    for raw in list(rows or []):
        if isinstance(raw, dict):
            text = str(raw.get("text", "") or "").strip()
            ts = raw.get("ts")
            source = str(raw.get("source", "phone") or "phone").strip().lower()
        elif isinstance(raw, str):
            text = raw.strip()
            ts = None
            source = "unknown"
        else:
            continue
        if not text:
            continue
        if text == prev_text:
            continue
        try:
            ts_i = int(ts) if ts is not None else now
        except Exception:
            ts_i = now
        if ts_i > 10_000_000_000:
            ts_i //= 1000
        if source not in {"phone", "pc", "unknown"}:
            source = "unknown"
        out.append({
            "text": text,
            "ts": ts_i,
            "source": source,
        })
        prev_text = text
    return out[-max(1, int(limit)):]
