#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    root = Path.cwd()
    py = root / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)

    env = os.environ.copy()
    sys_site = ""
    try:
        probe = subprocess.run(
            ["python3", "-c", "import site; print(site.getsitepackages()[0])"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            sys_site = (probe.stdout or "").strip()
    except Exception:
        sys_site = ""
    if sys_site:
        current = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{sys_site}:{current}" if current else sys_site

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts_dir = root / "tests" / "hardware" / ".artifacts"
    summary_path = artifacts_dir / f"live_acceptance_{ts}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    run_dir = artifacts_dir / f"live_acceptance_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    steps = [
        {
            "name": "popup_smoke",
            "cmd": [str(py), "scripts/test_call_popup.py", "--caller", "PB Live Harness", "--number", "+15558675309"],
            "report": None,
        },
        {
            "name": "call_route",
            "cmd": [str(py), "tests/hardware/call_mic_harness.py", "--report", str(run_dir / "call_route.json")],
            "report": run_dir / "call_route.json",
        },
        {
            "name": "notification_flow",
            "cmd": [str(py), "tests/hardware/notification_flow_harness.py", "--report", str(run_dir / "notification_flow.json")],
            "report": run_dir / "notification_flow.json",
        },
        {
            "name": "files_flow",
            "cmd": [str(py), "tests/hardware/files_flow_harness.py", "--report", str(run_dir / "files_flow.json")],
            "report": run_dir / "files_flow.json",
        },
        {
            "name": "hyprland_toggle",
            "cmd": [str(py), "tests/hardware/hyprland_toggle_harness.py", "--report", str(run_dir / "hyprland_toggle.json")],
            "report": run_dir / "hyprland_toggle.json",
        },
    ]

    summary = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS",
        "failed_step": "",
        "steps": [],
    }

    for step in steps:
        proc = subprocess.run(step["cmd"], cwd=root, capture_output=True, text=True, check=False, env=env)
        entry = {
            "name": step["name"],
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
        report_path = step.get("report")
        if report_path:
            report_path = Path(report_path)
            entry["report_path"] = str(report_path)
            if report_path.exists():
                try:
                    entry["report"] = json.loads(report_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    entry["report_error"] = str(exc)
        summary["steps"].append(entry)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        if proc.returncode != 0:
            summary["status"] = "FAIL"
            summary["failed_step"] = step["name"]
            summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
            print(f"live-acceptance: failed at {step['name']}")
            print(f"summary_report: {summary_path}")
            return proc.returncode

    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("live-acceptance: ok")
    print(f"summary_report: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
