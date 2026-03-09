#!/usr/bin/env python3
"""Deterministic live notification-flow acceptance for PhoneBridge."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.hardware.harness_common import LOG_PATH, log_offset, run, utc_timestamp, wait_for_log, wait_until, write_report
from backend.adb_bridge import ADBBridge


def _adb_cmd(*args: str) -> list[str]:
    cmd = ["adb"]
    serial = str(ADBBridge().resolve_target(allow_connect=True) or "").strip()
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return cmd


def _adb_shell(command: str, *, timeout: float = 8.0) -> dict:
    return run(_adb_cmd("shell", command), timeout=timeout)


def _notification_keys() -> list[str]:
    out = _adb_shell("cmd notification list", timeout=4.0)
    if not out["ok"]:
        return []
    return [line.strip() for line in str(out["stdout"] or "").splitlines() if line.strip()]


def _find_notification_key(tag: str) -> str | None:
    needle = f"|{tag}|"
    for line in _notification_keys():
        if needle in line:
            return line
    return None


def _wait_for_notification_key(tag: str, *, present: bool, timeout: float = 8.0) -> bool:
    def _probe():
        key = _find_notification_key(tag)
        return key if present else (key is None)

    result = wait_until(_probe, timeout=timeout, step=0.25)
    return bool(result)


def _post_notification(tag: str, title: str, text: str) -> dict:
    return _adb_shell(f'cmd notification post -t "{title}" "{tag}" "{text}"', timeout=5.0)


def _snooze_notification(key: str) -> dict:
    escaped = key.replace('"', '\\"')
    return _adb_shell(f'cmd notification snooze --for 60000 "{escaped}"', timeout=5.0)


def _start_monitor() -> subprocess.Popen:
    return subprocess.Popen(
        ["dbus-monitor", "--session", "interface='org.freedesktop.Notifications'"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop_monitor(proc: subprocess.Popen) -> str:
    try:
        time.sleep(0.6)
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=3)
    except Exception:
        proc.kill()
        stdout, stderr = proc.communicate()
    return "\n".join(part for part in (stdout, stderr) if part)


def _count_notify_blocks(monitor_output: str, token: str) -> int:
    blocks: list[str] = []
    current: list[str] = []
    for line in str(monitor_output or "").splitlines():
        if "member=Notify" in line and "method call" in line:
            if current:
                blocks.append("\n".join(current))
            current = [line]
            continue
        if current:
            if line.startswith("method call") and "member=Notify" not in line:
                blocks.append("\n".join(current))
                current = []
                continue
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return sum(1 for block in blocks if token in block)


def _extract_ids(line: str) -> tuple[str, int]:
    match = re.search(r"phone_id=(\S+)\s+desktop_id=(\d+)", line or "")
    if not match:
        raise RuntimeError(f"Could not parse mirrored notification ids from log line: {line}")
    return match.group(1), int(match.group(2))


def main() -> int:
    parser = argparse.ArgumentParser(description="PhoneBridge live notification-flow harness")
    parser.add_argument(
        "--report",
        default="tests/hardware/notification_flow_report.json",
        help="Path for JSON report output.",
    )
    args = parser.parse_args()

    report = {
        "timestamp_utc": utc_timestamp(),
        "checks": {},
        "results": {},
    }

    for tool in ("adb", "dbus-monitor", "gdbus"):
        probe = run(["bash", "-lc", f"command -v {tool}"], timeout=2.0)
        report["checks"][f"tool_{tool}"] = probe
        if not probe["ok"]:
            report["results"]["summary"] = f"FAIL_MISSING_{tool.upper()}"
            write_report(args.report, report)
            print(f"summary: {report['results']['summary']}")
            print(f"json_report: {args.report}")
            return 1

    token = str(int(time.time() * 1000))
    title1 = f"PB Harness {token}"
    text1 = f"Notification flow {token}"
    tag1 = f"pb_harness_{token}"
    title2 = f"PB Harness {token}-b"
    text2 = f"Notification flow {token}-b"
    tag2 = f"pb_harness_{token}_b"

    offset = log_offset()
    monitor = _start_monitor()
    post1 = _post_notification(tag1, title1, text1)
    report["checks"]["post_first"] = post1
    if not post1["ok"]:
        report["results"]["summary"] = "FAIL_POST_FIRST"
        write_report(args.report, report)
        return 1

    key1 = wait_until(lambda: _find_notification_key(tag1), timeout=8.0, step=0.25)
    if not key1:
        report["results"]["summary"] = "FAIL_PHONE_KEY_FIRST"
        write_report(args.report, report)
        return 1
    report["results"]["phone_key_first"] = key1

    mirrored_line, offset = wait_for_log(rf"Mirrored notification .*title=.*{re.escape(token)}", timeout=10.0, offset=offset)
    if not mirrored_line:
        report["results"]["summary"] = "FAIL_MIRROR_FIRST"
        write_report(args.report, report)
        return 1
    phone_id1, desktop_id1 = _extract_ids(mirrored_line)
    report["results"]["mirror_first"] = {
        "phone_id": phone_id1,
        "desktop_id": desktop_id1,
        "log": mirrored_line,
    }

    monitor_output = _stop_monitor(monitor)
    visible_count = _count_notify_blocks(monitor_output, token)
    report["results"]["visible_notify_count"] = visible_count
    if visible_count != 1:
        report["results"]["summary"] = "FAIL_DUPLICATE_VISIBLE_NOTIFY"
        write_report(args.report, report)
        return 1

    action = run(
        [
            "dbus-send",
            "--session",
            "--type=signal",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications.ActionInvoked",
            f"uint32:{desktop_id1}",
            "string:default",
        ],
        timeout=4.0,
    )
    report["checks"]["invoke_default_action"] = action
    if not action["ok"]:
        report["results"]["summary"] = "FAIL_DEFAULT_ACTION_SIGNAL"
        write_report(args.report, report)
        return 1

    action_offset = offset
    open_line, offset = wait_for_log(rf"Notification open request id={re.escape(phone_id1)}", timeout=8.0, offset=action_offset)
    goto_line, _ = wait_for_log(r"Window go_to page=messages", timeout=8.0, offset=action_offset)
    report["results"]["open_request_log"] = open_line
    report["results"]["goto_messages_log"] = goto_line
    if not open_line or not goto_line:
        report["results"]["summary"] = "FAIL_DEFAULT_OPEN_BEHAVIOR"
        write_report(args.report, report)
        return 1

    close_panel = run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.CloseNotification",
            str(desktop_id1),
        ],
        timeout=4.0,
    )
    report["checks"]["close_from_panel"] = close_panel
    if not close_panel["ok"]:
        report["results"]["summary"] = "FAIL_PANEL_CLOSE"
        write_report(args.report, report)
        return 1

    panel_closed = wait_for_log(rf"Desktop notification closed desktop_id={desktop_id1}\s+phone_id={re.escape(phone_id1)}", timeout=10.0, offset=offset)[0]
    phone_removed = _wait_for_notification_key(tag1, present=False, timeout=10.0)
    report["results"]["panel_closed_log"] = panel_closed
    report["results"]["phone_removed_after_panel_close"] = phone_removed
    if not panel_closed or not phone_removed:
        report["results"]["summary"] = "FAIL_PANEL_TO_PHONE_DISMISS"
        write_report(args.report, report)
        return 1

    post2 = _post_notification(tag2, title2, text2)
    report["checks"]["post_second"] = post2
    if not post2["ok"]:
        report["results"]["summary"] = "FAIL_POST_SECOND"
        write_report(args.report, report)
        return 1

    key2 = wait_until(lambda: _find_notification_key(tag2), timeout=8.0, step=0.25)
    if not key2:
        report["results"]["summary"] = "FAIL_PHONE_KEY_SECOND"
        write_report(args.report, report)
        return 1
    report["results"]["phone_key_second"] = key2

    mirrored_line2, offset = wait_for_log(rf"Mirrored notification .*title=.*{re.escape(token)}-b", timeout=10.0, offset=offset)
    if not mirrored_line2:
        report["results"]["summary"] = "FAIL_MIRROR_SECOND"
        write_report(args.report, report)
        return 1
    phone_id2, desktop_id2 = _extract_ids(mirrored_line2)
    report["results"]["mirror_second"] = {
        "phone_id": phone_id2,
        "desktop_id": desktop_id2,
        "log": mirrored_line2,
    }

    snooze = _snooze_notification(key2)
    report["checks"]["phone_side_snooze"] = snooze
    if not snooze["ok"]:
        report["results"]["summary"] = "FAIL_PHONE_SIDE_DISMISS"
        write_report(args.report, report)
        return 1

    close_phone_line, offset = wait_for_log(
        rf"Closing mirrored notification phone_id={re.escape(phone_id2)} desktop_id={desktop_id2} origin=phone_removed",
        timeout=10.0,
        offset=offset,
    )
    removed2 = _wait_for_notification_key(tag2, present=False, timeout=10.0)
    report["results"]["phone_removed_log"] = close_phone_line
    report["results"]["phone_removed_after_phone_side"] = removed2
    if not close_phone_line or not removed2:
        report["results"]["summary"] = "FAIL_PHONE_TO_PANEL_DISMISS"
        write_report(args.report, report)
        return 1

    report["results"]["summary"] = "PASS_NOTIFICATION_FLOW"
    write_report(args.report, report)
    print("PhoneBridge notification-flow harness")
    print(f"log_path: {LOG_PATH}")
    print(f"summary: {report['results']['summary']}")
    print(f"json_report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
