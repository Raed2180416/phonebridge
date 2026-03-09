"""Tests for immutable runtime publishing and systemd unit generation."""

from __future__ import annotations

from pathlib import Path

import backend.autostart as autostart


def _make_project(root: Path):
    (root / "backend").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("print('phonebridge')\n", encoding="utf-8")
    (root / "run-venv-nix.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / "scripts" / "phonebridge-toggle.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (root / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (root / ".venv" / "bin" / "python").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")


def test_write_unit_file_points_to_runtime_current(tmp_path, monkeypatch):
    project = tmp_path / "project"
    runtime_base = tmp_path / "runtime"
    unit_path = tmp_path / "systemd" / "user" / autostart.UNIT_NAME
    _make_project(project)

    monkeypatch.setattr(autostart, "_runtime_base_path", lambda: runtime_base)
    monkeypatch.setattr(autostart, "_unit_path", lambda: unit_path)

    written = autostart.write_unit_file(str(project))

    assert written == unit_path
    current = runtime_base / "current"
    launcher = current / "run-venv-runtime.sh"
    text = unit_path.read_text(encoding="utf-8")

    assert f"WorkingDirectory={current}" in text
    assert f"ExecStart={launcher} --background" in text
    assert (current / "main.py").exists()
    assert launcher.exists()
    assert not (current / ".venv").exists()


def test_preferred_launcher_uses_current_runtime_when_available(tmp_path, monkeypatch):
    runtime_base = tmp_path / "runtime"
    current = runtime_base / "current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "run-venv-runtime.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "run-venv-nix.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    monkeypatch.setattr(autostart, "_runtime_base_path", lambda: runtime_base)

    assert autostart.preferred_launcher(project) == current / "run-venv-runtime.sh"


def test_publish_runtime_refreshes_existing_desktop_entry(tmp_path, monkeypatch):
    project = tmp_path / "project"
    runtime_base = tmp_path / "runtime"
    _make_project(project)

    desktop_calls = []

    monkeypatch.setattr(autostart, "_runtime_base_path", lambda: runtime_base)
    monkeypatch.setattr(
        "backend.system_integration.refresh_desktop_entry_if_present",
        lambda root: desktop_calls.append(Path(root)) or (True, "desktop"),
    )

    current, launcher = autostart.publish_runtime(str(project))

    assert current == runtime_base / "current"
    assert launcher == current / "run-venv-runtime.sh"
    assert desktop_calls == [project.resolve()]


def test_publish_runtime_failure_does_not_move_current(tmp_path, monkeypatch):
    project = tmp_path / "project"
    runtime_base = tmp_path / "runtime"
    _make_project(project)
    current = runtime_base / "current"
    old_release = runtime_base / "release-old"
    old_release.mkdir(parents=True, exist_ok=True)
    (old_release / "marker.txt").write_text("old\n", encoding="utf-8")
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_release, target_is_directory=True)

    monkeypatch.setattr(autostart, "_runtime_base_path", lambda: runtime_base)
    monkeypatch.setattr(autostart, "_write_text_atomic", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))

    try:
        autostart.publish_runtime(str(project))
    except RuntimeError:
        pass
    else:
        raise AssertionError("publish_runtime should fail")

    assert current.resolve() == old_release.resolve()
