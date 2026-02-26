"""UI feedback helpers (toasts)."""
from __future__ import annotations

import time
from backend.state import state


def push_toast(message: str, level: str = "info", ttl_ms: int = 2200):
    msg = (message or "").strip()
    if not msg:
        return
    queue = list(state.get("ui_toast_queue", []) or [])
    queue.append({
        "text": msg,
        "level": level,
        "ttl_ms": int(ttl_ms),
        "ts": int(time.time() * 1000),
    })
    queue = queue[-20:]
    state.set("ui_toast_queue", queue)
