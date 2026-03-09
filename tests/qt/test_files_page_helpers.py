"""Headless Qt coverage for FilesPage test helper seams."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.qt_runtime


@pytest.fixture
def app():
    sys.modules.pop("PyQt6", None)
    sys.modules.pop("PyQt6.QtCore", None)
    sys.modules.pop("PyQt6.QtWidgets", None)
    qtwidgets = pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
    QApplication = qtwidgets.QApplication
    inst = QApplication.instance()
    if inst is None:
        inst = QApplication([])
    return inst


def test_files_page_test_helpers_manage_custom_folder_and_subdir(monkeypatch, tmp_path: Path, app):
    sys.modules.pop("ui.pages.files", None)
    files = importlib.import_module("ui.pages.files")

    class _KC:
        pass

    class _ST:
        pass

    saved = []
    monkeypatch.setattr(files, "KDEConnect", _KC)
    monkeypatch.setattr(files, "Syncthing", _ST)
    monkeypatch.setattr(files.FilesPage, "refresh", lambda self: None)
    monkeypatch.setattr(files.settings, "set_many", lambda payload: saved.append(dict(payload)))

    page = files.FilesPage()
    folder_root = tmp_path / "fixture"
    folder_root.mkdir()
    assert page.add_custom_folder_for_test("qt-folder", "Qt Folder", str(folder_root))
    assert page._folder_by_id("qt-folder") is not None
    assert saved

    opened = []
    monkeypatch.setattr(page, "_open_folder", lambda folder: opened.append(dict(folder)))
    assert page.open_folder_by_id("qt-folder")
    assert opened and opened[-1]["id"] == "qt-folder"

    assert page.create_subfolder_for_test("qt-folder", "child")
    assert (folder_root / "child").exists()

    assert page.remove_custom_folder_for_test("qt-folder")
    assert page._folder_by_id("qt-folder") is None


def test_files_page_renders_folder_payload_without_runtime_name_errors(monkeypatch, tmp_path: Path, app):
    sys.modules.pop("ui.pages.files", None)
    files = importlib.import_module("ui.pages.files")

    class _KC:
        pass

    class _ST:
        pass

    monkeypatch.setattr(files, "KDEConnect", _KC)
    monkeypatch.setattr(files, "Syncthing", _ST)
    monkeypatch.setattr(files.FilesPage, "refresh", lambda self: None)

    page = files.FilesPage()
    page._load_worker = None
    page._load_token = 7

    folder_root = tmp_path / "fixture"
    folder_root.mkdir()
    sample = folder_root / "notes.txt"
    sample.write_text("fixture\n", encoding="utf-8")

    payload = {
        "token": 7,
        "mode": "folder",
        "folder": {"id": "qt-folder", "name": "Qt Folder", "path": str(folder_root)},
        "entries": [
            {
                "name": "notes.txt",
                "full": str(sample),
                "size": sample.stat().st_size,
                "thumb_path": None,
            }
        ],
        "truncated": False,
    }

    page._apply_loaded_payload(payload)

    assert page._current_folder["id"] == "qt-folder"
    assert page.layout().count() >= 1


def test_files_page_disables_periodic_and_runtime_status_auto_refresh(monkeypatch, app):
    sys.modules.pop("ui.pages.files", None)
    files = importlib.import_module("ui.pages.files")

    class _KC:
        pass

    class _ST:
        pass

    monkeypatch.setattr(files, "KDEConnect", _KC)
    monkeypatch.setattr(files, "Syncthing", _ST)
    monkeypatch.setattr(files.FilesPage, "refresh", lambda self: None)

    page = files.FilesPage()

    assert page.allow_periodic_refresh is False
    assert page.allow_runtime_status_refresh is False
