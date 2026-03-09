"""Developer-only auto-publish watcher for installed runtime sync."""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import threading
import time
from typing import Callable

from backend import autostart

log = logging.getLogger(__name__)

IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".idea",
    ".vscode",
    ".local",
}

IGNORED_REL_PREFIXES = (
    "tests/hardware/.artifacts/",
)

IGNORED_FILE_SUFFIXES = (".pyc", ".pyo", ".swp", ".tmp")


def should_ignore_relpath(rel_path: str) -> bool:
    norm = str(rel_path or "").replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    norm = norm.rstrip("/")
    if not norm:
        return False
    parts = [p for p in norm.split("/") if p]
    if any(part in IGNORED_DIR_NAMES for part in parts):
        return True
    if any(norm.startswith(prefix) for prefix in IGNORED_REL_PREFIXES):
        return True
    if any(norm.endswith(suffix) for suffix in IGNORED_FILE_SUFFIXES):
        return True
    return False


def build_tree_signature(root: Path) -> dict[str, tuple[int, int]]:
    root = Path(root).resolve()
    snapshot: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)
        rel_dir = current_dir.relative_to(root).as_posix() if current_dir != root else ""
        dirnames[:] = [
            name
            for name in dirnames
            if not should_ignore_relpath(f"{rel_dir}/{name}" if rel_dir else name)
        ]
        for name in filenames:
            rel_path = f"{rel_dir}/{name}" if rel_dir else name
            if should_ignore_relpath(rel_path):
                continue
            full_path = current_dir / name
            try:
                stat = full_path.stat()
            except OSError:
                continue
            snapshot[rel_path] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def publish_and_restart(project_root: Path) -> tuple[bool, str]:
    current, launcher = autostart.publish_runtime(str(project_root))
    ok, msg = autostart.restart_running_app(launcher)
    if not ok:
        return False, msg
    return True, f"published {current} and restarted app"


class RuntimeWatchLoop:
    def __init__(
        self,
        *,
        root: Path,
        debounce_s: float = 1.2,
        poll_s: float = 0.75,
        snapshot_fn: Callable[[Path], dict[str, tuple[int, int]]] = build_tree_signature,
        publish_fn: Callable[[Path], tuple[bool, str]] = publish_and_restart,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = Path(root).resolve()
        self.debounce_s = max(0.1, float(debounce_s))
        self.poll_s = max(0.1, float(poll_s))
        self._snapshot_fn = snapshot_fn
        self._publish_fn = publish_fn
        self._clock_fn = clock_fn
        self._last_applied: dict[str, tuple[int, int]] | None = None
        self._pending: dict[str, tuple[int, int]] | None = None
        self._pending_since = 0.0
        self._publish_lock = threading.Lock()

    def tick(self) -> str:
        snapshot = self._snapshot_fn(self.root)
        now = self._clock_fn()
        if self._last_applied is None:
            self._last_applied = snapshot
            return "primed"
        if snapshot != self._last_applied:
            if snapshot != self._pending:
                self._pending = snapshot
                self._pending_since = now
                return "change-detected"
            if (now - self._pending_since) < self.debounce_s:
                return "debouncing"
            if not self._publish_lock.acquire(blocking=False):
                return "publish-busy"
            try:
                ok, detail = self._publish_fn(self.root)
                if ok:
                    self._last_applied = snapshot
                    self._pending = None
                    self._pending_since = 0.0
                    log.info("Dev runtime watcher publish succeeded: %s", detail)
                    return "published"
                log.warning("Dev runtime watcher publish failed: %s", detail)
                return "publish-failed"
            finally:
                self._publish_lock.release()
        self._pending = None
        self._pending_since = 0.0
        return "idle"

    def run_forever(self) -> None:
        log.info(
            "Dev runtime watcher started root=%s debounce=%.2fs poll=%.2fs",
            self.root,
            self.debounce_s,
            self.poll_s,
        )
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("Dev runtime watcher tick failed")
            time.sleep(self.poll_s)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-publish PhoneBridge runtime from repo edits")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--debounce", type=float, default=1.2)
    parser.add_argument("--poll", type=float, default=0.75)
    parser.add_argument("--once", action="store_true", help="Run a single watcher tick and exit")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print watcher config and exit without running",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = Path(args.root).resolve()
    watcher = RuntimeWatchLoop(root=root, debounce_s=args.debounce, poll_s=args.poll)
    if args.self_check:
        print(f"root={root}")
        print(f"debounce={watcher.debounce_s}")
        print(f"poll={watcher.poll_s}")
        return 0
    if args.once:
        print(watcher.tick())
        return 0
    watcher.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
