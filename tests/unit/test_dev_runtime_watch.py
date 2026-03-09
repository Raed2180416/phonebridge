from __future__ import annotations

import os
from pathlib import Path
import subprocess
import shutil

from backend import dev_runtime_watch


def test_should_ignore_relpath_filters_noise():
    assert dev_runtime_watch.should_ignore_relpath(".git/index")
    assert dev_runtime_watch.should_ignore_relpath(".venv/bin/python")
    assert dev_runtime_watch.should_ignore_relpath("tests/hardware/.artifacts/live.json")
    assert dev_runtime_watch.should_ignore_relpath("ui/__pycache__/x.pyc")
    assert not dev_runtime_watch.should_ignore_relpath("ui/window.py")


def test_runtime_watch_loop_debounces_and_publishes_once(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    states = iter(
        [
            {"a.py": (1, 10)},
            {"a.py": (2, 10)},
            {"a.py": (2, 10)},
            {"a.py": (2, 10)},
        ]
    )
    now = {"t": 0.0}
    publish_calls: list[Path] = []

    watcher = dev_runtime_watch.RuntimeWatchLoop(
        root=root,
        debounce_s=1.0,
        snapshot_fn=lambda _root: next(states),
        publish_fn=lambda path: publish_calls.append(path) or (True, "ok"),
        clock_fn=lambda: now["t"],
    )

    assert watcher.tick() == "primed"
    now["t"] = 0.2
    assert watcher.tick() == "change-detected"
    now["t"] = 0.8
    assert watcher.tick() == "debouncing"
    now["t"] = 1.4
    assert watcher.tick() == "published"
    assert publish_calls == [root.resolve()]


def test_runtime_watch_loop_drops_failed_publish_without_advancing(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    states = iter(
        [
            {"a.py": (1, 10)},
            {"a.py": (2, 10)},
            {"a.py": (2, 10)},
            {"a.py": (2, 10)},
            {"a.py": (2, 10)},
        ]
    )
    now = {"t": 0.0}
    calls = {"publish": 0}

    def _publish(_path: Path):
        calls["publish"] += 1
        return False, "boom"

    watcher = dev_runtime_watch.RuntimeWatchLoop(
        root=root,
        debounce_s=1.0,
        snapshot_fn=lambda _root: next(states),
        publish_fn=_publish,
        clock_fn=lambda: now["t"],
    )

    assert watcher.tick() == "primed"
    now["t"] = 0.2
    assert watcher.tick() == "change-detected"
    now["t"] = 1.3
    assert watcher.tick() == "publish-failed"
    now["t"] = 2.5
    assert watcher.tick() == "publish-failed"
    assert calls["publish"] == 2


def test_build_tree_signature_ignores_artifacts(tmp_path):
    root = tmp_path / "project"
    (root / "ui").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "tests" / "hardware" / ".artifacts").mkdir(parents=True)
    (root / "ui" / "window.py").write_text("x=1\n", encoding="utf-8")
    (root / ".git" / "index").write_text("ignored\n", encoding="utf-8")
    (root / "tests" / "hardware" / ".artifacts" / "live.json").write_text("ignored\n", encoding="utf-8")

    snapshot = dev_runtime_watch.build_tree_signature(root)

    assert "ui/window.py" in snapshot
    assert ".git/index" not in snapshot
    assert "tests/hardware/.artifacts/live.json" not in snapshot


def test_dev_runtime_watch_script_runs_without_bash_on_path():
    script = Path("scripts/dev_runtime_watch.sh").resolve()
    env = os.environ.copy()
    dirname_bin = Path(shutil.which("dirname")).resolve().parent
    env["PATH"] = str(dirname_bin)

    proc = subprocess.run(
        [str(script), "--self-check"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "root=" in proc.stdout
