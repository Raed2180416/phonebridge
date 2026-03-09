#!/usr/bin/env python3
"""Scripted hardware verification harness for call audio + mic routing."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import audio_route
from backend.adb_bridge import ADBBridge


def _run(cmd, timeout=4):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "cmd": cmd,
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "cmd": cmd,
    }


def _have(cmd_name: str) -> bool:
    return shutil.which(cmd_name) is not None


def _yesno(val: bool) -> str:
    return "yes" if bool(val) else "no"


def main():
    parser = argparse.ArgumentParser(description="PhoneBridge call/mic hardware verification harness")
    parser.add_argument(
        "--serial",
        default="",
        help="ADB device serial to target (recommended when multiple devices are connected).",
    )
    parser.add_argument(
        "--no-route-mutation",
        action="store_true",
        help="Only inspect environment and avoid toggling audio_route sources.",
    )
    parser.add_argument(
        "--report",
        default="tests/hardware/call_mic_report.json",
        help="Path for JSON report output.",
    )
    args = parser.parse_args()

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "checks": {},
        "results": {},
    }

    tools = {
        "adb": _have("adb"),
        "pactl": _have("pactl"),
        "wpctl": _have("wpctl"),
        "scrcpy": _have("scrcpy"),
    }
    report["checks"]["tooling"] = tools

    if tools["adb"]:
        adb_cmd = ["adb"]
        serial = str(args.serial or "").strip() or str(ADBBridge().resolve_target(allow_connect=True) or "").strip()
        if serial:
            adb_cmd += ["-s", serial]
        report["checks"]["adb_get_state"] = _run(adb_cmd + ["get-state"], timeout=3)
    else:
        report["checks"]["adb_get_state"] = {"ok": False, "reason": "adb missing"}

    if tools["wpctl"]:
        wpctl_status = _run(["wpctl", "status"], timeout=4)
        report["checks"]["wpctl_status"] = wpctl_status
        lower = (wpctl_status.get("stdout") or "").lower()
        report["results"]["bluez_input_visible"] = "bluez_input." in lower
        report["results"]["hfp_or_hsp_visible"] = any(
            token in lower for token in ("handsfree", "headset", "hfp", "hsp", "audio-gateway", "audio_gateway")
        )
    else:
        report["checks"]["wpctl_status"] = {"ok": False, "reason": "wpctl missing"}
        report["results"]["bluez_input_visible"] = False
        report["results"]["hfp_or_hsp_visible"] = False

    if tools["pactl"]:
        report["checks"]["pactl_sources"] = _run(["pactl", "list", "short", "sources"], timeout=4)
        report["checks"]["pactl_sinks"] = _run(["pactl", "list", "short", "sinks"], timeout=4)
    else:
        report["checks"]["pactl_sources"] = {"ok": False, "reason": "pactl missing"}
        report["checks"]["pactl_sinks"] = {"ok": False, "reason": "pactl missing"}

    prev_sources = audio_route.current_sources()
    route_attempt = {
        "skipped": bool(args.no_route_mutation),
        "ok": None,
        "backend": "none",
        "status": "skipped" if args.no_route_mutation else "unknown",
        "reason": "",
    }

    if not args.no_route_mutation:
        try:
            audio_route.set_source("ui_global_toggle", False)
            audio_route.set_source("call_pc_active", True)
            res = audio_route.sync_result(call_retry_ms=8000, retry_step_ms=300)
            route_attempt.update(
                {
                    "ok": bool(res.ok),
                    "backend": str(res.backend),
                    "status": str(res.status),
                    "reason": str(res.reason),
                }
            )
        finally:
            # Always restore prior sources to keep this harness removable/non-invasive.
            audio_route.set_source("call_pc_active", bool(prev_sources.get("call_pc_active", False)))
            audio_route.set_source("ui_global_toggle", bool(prev_sources.get("ui_global_toggle", False)))
            audio_route.sync_result(call_retry_ms=0)

    report["results"]["route_attempt"] = route_attempt

    if route_attempt.get("skipped"):
        report["results"]["summary"] = "INSPECT_ONLY"
    elif route_attempt.get("ok") and route_attempt.get("backend") == "external_bt":
        report["results"]["summary"] = "PASS_CALL_ROUTE_WITH_MIC_PATH"
    elif route_attempt.get("ok"):
        report["results"]["summary"] = "PARTIAL_CALL_ROUTE_ACTIVE"
    elif route_attempt.get("status") == "pending":
        report["results"]["summary"] = "PENDING_CALL_ROUTE"
    else:
        report["results"]["summary"] = "FAIL_CALL_ROUTE"

    out_path = Path(args.report)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("PhoneBridge hardware call/mic harness")
    print(f"timestamp: {report['timestamp_utc']}")
    print(f"tooling: adb={_yesno(tools['adb'])} pactl={_yesno(tools['pactl'])} wpctl={_yesno(tools['wpctl'])} scrcpy={_yesno(tools['scrcpy'])}")
    print(f"bluez_input_visible: {_yesno(report['results'].get('bluez_input_visible'))}")
    print(f"hfp_or_hsp_visible: {_yesno(report['results'].get('hfp_or_hsp_visible'))}")
    if route_attempt.get("skipped"):
        print("route_attempt: skipped (--no-route-mutation)")
    else:
        print(
            "route_attempt: "
            f"ok={_yesno(route_attempt.get('ok'))} "
            f"backend={route_attempt.get('backend')} "
            f"status={route_attempt.get('status')} "
            f"reason={route_attempt.get('reason')}"
        )
    print(f"summary: {report['results']['summary']}")
    print(f"json_report: {out_path}")


if __name__ == "__main__":
    main()
