#!/usr/bin/env python3
"""Deterministic Hyprland toggle acceptance for PhoneBridge."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.hardware.harness_common import run, send_ipc, utc_timestamp, wait_until, write_report


def _hypr_json(*args: str) -> dict | list | None:
    result = run(["hyprctl", "-j", *args], timeout=4.0)
    if not result["ok"]:
        return None
    try:
        return json.loads(result["stdout"] or "null")
    except Exception:
        return None


def _active_window_matches() -> bool:
    row = _hypr_json("activewindow") or {}
    title = str((row or {}).get("title") or "")
    klass = str((row or {}).get("class") or "")
    initial_class = str((row or {}).get("initialClass") or "")
    initial_title = str((row or {}).get("initialTitle") or "")
    return (
        klass == "phonebridge"
        or initial_class == "phonebridge"
        or title == "PhoneBridge"
        or initial_title == "PhoneBridge"
    )


def _phonebridge_client() -> dict | None:
    clients = _hypr_json("clients") or []
    for row in clients:
        title = str((row or {}).get("title") or "")
        klass = str((row or {}).get("class") or "")
        initial_class = str((row or {}).get("initialClass") or "")
        initial_title = str((row or {}).get("initialTitle") or "")
        if (
            klass == "phonebridge"
            or initial_class == "phonebridge"
            or title == "PhoneBridge"
            or initial_title == "PhoneBridge"
        ):
            return row
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="PhoneBridge Hyprland toggle harness")
    parser.add_argument(
        "--report",
        default="tests/hardware/hyprland_toggle_report.json",
        help="Path for JSON report output.",
    )
    args = parser.parse_args()

    report = {
        "timestamp_utc": utc_timestamp(),
        "checks": {},
        "results": {},
    }

    toggle_script = Path("scripts/phonebridge-toggle.sh").resolve()
    if not toggle_script.exists():
        report["results"]["summary"] = "FAIL_MISSING_TOGGLE_SCRIPT"
        write_report(args.report, report)
        return 1

    tool_probe = run(["bash", "-lc", "command -v hyprctl"], timeout=2.0)
    report["checks"]["tool_hyprctl"] = tool_probe
    if not tool_probe["ok"]:
        report["results"]["summary"] = "FAIL_MISSING_HYPRCTL"
        write_report(args.report, report)
        return 1

    send_ipc("show")
    shown = wait_until(_active_window_matches, timeout=6.0, step=0.2)
    report["results"]["initial_show"] = bool(shown)
    if not shown:
        report["results"]["summary"] = "FAIL_INITIAL_SHOW"
        write_report(args.report, report)
        return 1

    hide_result = run([str(toggle_script)], timeout=5.0)
    report["checks"]["toggle_hide"] = hide_result
    hidden = wait_until(lambda: not _active_window_matches(), timeout=6.0, step=0.2)
    report["results"]["hidden_after_toggle"] = bool(hidden)
    if not hide_result["ok"] or not hidden:
        report["results"]["summary"] = "FAIL_HIDE_TOGGLE"
        write_report(args.report, report)
        return 1

    workspace_before = _hypr_json("activeworkspace") or {}
    show_result = run([str(toggle_script)], timeout=5.0)
    report["checks"]["toggle_show"] = show_result
    shown_again = wait_until(_active_window_matches, timeout=6.0, step=0.2)
    client = _phonebridge_client()
    current_workspace = _hypr_json("activeworkspace") or {}
    workspace_id = None
    if isinstance(client, dict):
        workspace = client.get("workspace") or {}
        workspace_id = int((workspace or {}).get("id") or -1)
    expected_workspace_id = int((workspace_before or {}).get("id") or -1)
    current_workspace_id = int((current_workspace or {}).get("id") or -1)
    report["results"]["shown_after_toggle"] = bool(shown_again)
    report["results"]["expected_workspace_id"] = expected_workspace_id
    report["results"]["window_workspace_id"] = workspace_id
    report["results"]["current_workspace_id"] = current_workspace_id
    if not show_result["ok"] or not shown_again or workspace_id != expected_workspace_id or current_workspace_id != expected_workspace_id:
        report["results"]["summary"] = "FAIL_SHOW_TOGGLE_WORKSPACE"
        write_report(args.report, report)
        return 1

    report["results"]["summary"] = "PASS_HYPRLAND_TOGGLE"
    write_report(args.report, report)
    print("PhoneBridge Hyprland-toggle harness")
    print(f"summary: {report['results']['summary']}")
    print(f"json_report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
