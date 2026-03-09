#!/usr/bin/env python3
"""Deterministic live Files-page acceptance for PhoneBridge."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.hardware.harness_common import log_offset, run, send_ipc, utc_timestamp, wait_for_log, wait_until, write_report

SETTINGS_PATH = Path.home() / ".config" / "phonebridge" / "settings.json"

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WQ0bL8AAAAASUVORK5CYII="
)


def _load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _create_fixture_dir(base_dir: Path) -> tuple[Path, Path, Path]:
    base_dir.mkdir(parents=True, exist_ok=True)
    img_path = base_dir / "still.png"
    img_path.write_bytes(PNG_BYTES)
    video_path = base_dir / "clip.mp4"
    note_path = base_dir / "notes.txt"
    note_path.write_text("PhoneBridge file harness\n", encoding="utf-8")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for the Files hardware harness")
    result = run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=96x96:d=1",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        timeout=12.0,
    )
    if not result["ok"] or not video_path.exists():
        raise RuntimeError(f"ffmpeg failed to build sample clip: {result}")
    return img_path, video_path, note_path


def main() -> int:
    parser = argparse.ArgumentParser(description="PhoneBridge live Files-page harness")
    parser.add_argument(
        "--report",
        default="tests/hardware/files_flow_report.json",
        help="Path for JSON report output.",
    )
    args = parser.parse_args()

    report = {
        "timestamp_utc": utc_timestamp(),
        "checks": {},
        "results": {},
    }

    folder_id = f"pb-hw-files-{int(time.time() * 1000)}"
    folder_name = "PhoneBridge Harness Files"
    fixture_root = Path.home() / "PhoneBridgeHarness" / folder_id
    created_subdir = fixture_root / "from-phonebridge"
    cleanup_ok = True

    try:
        img_path, video_path, note_path = _create_fixture_dir(fixture_root)
        report["results"]["fixture_files"] = [str(img_path), str(video_path), str(note_path)]

        offset = log_offset()
        send_ipc({"cmd": "files_add_custom", "folder_id": folder_id, "name": folder_name, "path": str(fixture_root)})
        add_line, offset = wait_for_log(rf"IPC: files_add_custom folder_id={folder_id} ok=True", timeout=8.0, offset=offset)
        if not add_line:
            report["results"]["summary"] = "FAIL_ADD_CUSTOM_FOLDER"
            write_report(args.report, report)
            return 1

        send_ipc({"cmd": "files_open", "folder_id": folder_id})
        open_offset = offset
        open_line, offset = wait_for_log(rf"IPC: files_open folder_id={folder_id} ok=True", timeout=8.0, offset=open_offset)
        load_line, offset = wait_for_log(
            rf"Files page loaded folder .*folder_id={folder_id} entries=\d+ thumbs=(\d+)",
            timeout=12.0,
            offset=open_offset,
        )
        report["results"]["open_log"] = open_line
        report["results"]["load_log"] = load_line
        if not open_line or not load_line:
            report["results"]["summary"] = "FAIL_OPEN_OR_LOAD_FOLDER"
            write_report(args.report, report)
            return 1

        thumb_match = None
        if load_line:
            import re

            thumb_match = re.search(r"thumbs=(\d+)", load_line)
        thumb_count = int(thumb_match.group(1)) if thumb_match else 0
        report["results"]["thumb_count"] = thumb_count
        if thumb_count < 2:
            report["results"]["summary"] = "FAIL_THUMBNAIL_GENERATION"
            write_report(args.report, report)
            return 1

        send_ipc({"cmd": "files_mkdir", "folder_id": folder_id, "name": created_subdir.name})
        mkdir_offset = offset
        mkdir_line, offset = wait_for_log(
            rf"IPC: files_mkdir folder_id={folder_id} name={created_subdir.name} ok=True",
            timeout=8.0,
            offset=mkdir_offset,
        )
        created = wait_until(lambda: created_subdir.exists(), timeout=8.0, step=0.2)
        report["results"]["mkdir_log"] = mkdir_line
        report["results"]["created_subdir"] = str(created_subdir)
        if not created:
            report["results"]["summary"] = "FAIL_SUBFOLDER_MUTATION"
            write_report(args.report, report)
            return 1

        send_ipc({"cmd": "files_remove_custom", "folder_id": folder_id})
        remove_offset = offset
        remove_line, offset = wait_for_log(
            rf"IPC: files_remove_custom folder_id={folder_id} ok=True",
            timeout=8.0,
            offset=remove_offset,
        )
        settings_obj = _load_settings()
        custom_rows = list(settings_obj.get("custom_folders") or [])
        still_present = any(str((row or {}).get("id") or "") == folder_id for row in custom_rows)
        report["results"]["remove_log"] = remove_line
        report["results"]["custom_folder_still_present"] = still_present
        if still_present:
            report["results"]["summary"] = "FAIL_REMOVE_CUSTOM_FOLDER"
            write_report(args.report, report)
            return 1

        report["results"]["summary"] = "PASS_FILES_FLOW"
        write_report(args.report, report)
        print("PhoneBridge Files-flow harness")
        print(f"summary: {report['results']['summary']}")
        print(f"json_report: {args.report}")
        return 0
    except Exception as exc:
        report["results"]["summary"] = "FAIL_FILES_FLOW_EXCEPTION"
        report["results"]["error"] = str(exc)
        write_report(args.report, report)
        return 1
    finally:
        try:
            shutil.rmtree(fixture_root, ignore_errors=True)
        except Exception:
            cleanup_ok = False
        report.setdefault("results", {})["cleanup_ok"] = cleanup_ok
        write_report(args.report, report)


if __name__ == "__main__":
    raise SystemExit(main())
