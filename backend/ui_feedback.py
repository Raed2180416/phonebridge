"""UI feedback helpers (toasts)."""
from __future__ import annotations

import time
from backend.state import state


def push_toast(message: str, level: str = "info", ttl_ms: int = 2200):
    msg = (message or "").strip()
    if not msg:
        return
    state.update(
        "ui_toast_queue",
        lambda queue: (
            queue.append(
                {
                    "text": msg,
                    "level": level,
                    "ttl_ms": int(ttl_ms),
                    "ts": int(time.time() * 1000),
                }
            )
            or queue[-20:]
        ),
        default=[],
    )
