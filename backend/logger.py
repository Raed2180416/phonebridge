"""Central logging setup for PhoneBridge."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def setup_logging() -> str:
    """Initialize app-wide logging once and return the log file path."""
    global _CONFIGURED
    log_path = os.path.expanduser("~/.cache/phonebridge/phonebridge.log")
    if _CONFIGURED:
        return log_path

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Keep ui.window and backend.adb_bridge at DEBUG until call-poller
    # is confirmed stable in production; remove when happy.
    logging.getLogger("ui.window").setLevel(logging.DEBUG)
    logging.getLogger("backend.adb_bridge").setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    _CONFIGURED = True
    logging.getLogger(__name__).info("Logging initialized: %s", log_path)
    return log_path

