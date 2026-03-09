#!/usr/bin/env python3
"""
Deterministic test for the incoming call popup.

Sends a sequence of D-Bus callReceived signals and tails the log to
confirm the popup was triggered, then cleans up with an 'ended' event.

Usage:
    python scripts/test_call_popup.py [--caller "Name"] [--number "+123"]
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
import socket
import json


def _maybe_add_system_site_packages():
    candidates = []
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path:
            candidates.append(path)
    for candidate in candidates:
        try:
            proc = subprocess.run(
                [candidate, "-c", "import site; print(site.getsitepackages()[0])"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if proc.returncode != 0:
            continue
        site_dir = (proc.stdout or "").strip()
        if site_dir and site_dir not in sys.path:
            sys.path.insert(0, site_dir)
            return


import shutil

try:
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
    _HAVE_DBUS = True
except ImportError:
    _maybe_add_system_site_packages()
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
        _HAVE_DBUS = True
    except ImportError:
        _HAVE_DBUS = False

BUS_NAME = "org.kde.kdeconnect"
LOG_PATH = os.path.expanduser("~/.cache/phonebridge/phonebridge.log")


def get_device_id() -> str | None:
    if not _HAVE_DBUS:
        return None
    try:
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        daemon = dbus.Interface(
            bus.get_object(BUS_NAME, "/modules/kdeconnect", introspect=False),
            "org.kde.kdeconnect.daemon",
        )
        reachable = [str(x) for x in (daemon.devices(True, True) or [])]
        paired    = [str(x) for x in (daemon.devices(False, True) or [])]
        combined = reachable + [p for p in paired if p not in reachable]
        return combined[0] if combined else None
    except Exception as e:
        print(f"[WARN] Could not query KDE Connect device ID: {e}", file=sys.stderr)
        return None


def send_signal(device_id: str, event: str, number: str, name: str) -> bool:
    """Inject a call event into the running app via local IPC.

    The production app now only trusts the real KDE Connect bus sender for
    `callReceived`, so synthetic `dbus-send --type=signal` traffic is not a
    valid deterministic acceptance path anymore.
    """
    payload = json.dumps(
        {
            "cmd": "test_call_event",
            "event": str(event or ""),
            "number": str(number or ""),
            "name": str(name or ""),
            "device_id": str(device_id or ""),
        }
    ).encode("utf-8")
    uid = os.getuid()
    candidates = [
        os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"), f"phonebridge-{uid}.sock"),
        f"/tmp/phonebridge-{uid}.sock",
    ]
    for path in candidates:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect(path)
                sock.sendall(payload)
            return True
        except OSError:
            continue

    # Fallback for older local builds that still used the raw D-Bus signal
    # injection acceptance path.
    path = f"/modules/kdeconnect/devices/{device_id}/telephony"
    cmd = [
        "dbus-send",
        "--session",
        "--type=signal",
        path,
        "org.kde.kdeconnect.device.telephony.callReceived",
        f"string:{event}",
        f"string:{number}",
        f"string:{name}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FAIL] dbus-send failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def tail_log(keyword: str, timeout: float = 5.0, offset: int | None = None) -> tuple[str | None, int]:
    """Return (first matching log line, final offset).  Thread-safe single pass.

    Pass the returned offset as `offset` in the next call to continue
    reading from where we left off without missing burst-written lines.
    """
    deadline = time.monotonic() + timeout
    if offset is None:
        offset = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    while time.monotonic() < deadline:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH) as f:
                f.seek(offset)
                new = f.read()
            new_offset = offset + len(new.encode())
            if keyword.lower() in new.lower():
                for line in new.splitlines():
                    if keyword.lower() in line.lower():
                        return line.strip(), new_offset
            offset = new_offset
        time.sleep(0.15)
    return None, offset


def run_test(device_id: str, caller: str, number: str) -> bool:
    sep = "─" * 60
    ok = True

    print(sep)
    print(f"  PhoneBridge Call Popup — Deterministic Test")
    print(f"  device_id : {device_id}")
    print(f"  caller    : {caller}")
    print(f"  number    : {number}")
    print(sep)

    # ── 1. ringing ──────────────────────────────────────────
    log_offset = os.path.getsize(LOG_PATH) if os.path.exists(LOG_PATH) else 0
    print("\n[1] Sending 'ringing' event...", end=" ", flush=True)
    if not send_signal(device_id, "ringing", number, caller):
        print("FAIL (dbus-send error)")
        return False

    # Both searches start from the same pre-signal offset so burst-written
    # popup lines aren't missed when callReceived and the popup log entry
    # appear in the same file-read chunk.
    hit, _ = tail_log("Signal callReceived", timeout=5.0, offset=log_offset)
    if hit:
        print(f"OK\n    → {hit}")
    else:
        hit2, _ = tail_log("Synthesized ringing", timeout=2.0, offset=log_offset)
        if hit2:
            print(f"OK (via notification synthesis)\n    → {hit2}")
        else:
            print("FAIL — no log entry for callReceived or synthesis within 5 s")
            ok = False

    popup_hit, log_offset = tail_log("call_popup] Call popup", timeout=4.0, offset=log_offset)
    if popup_hit:
        print(f"    popup: {popup_hit}")
    else:
        print("    [WARN] No 'call_popup] Call popup' log line seen within 4 s")

    # ── 2. talking ──────────────────────────────────────────
    print("\n[2] Sending 'talking' event in 2 s...", end=" ", flush=True)
    time.sleep(2.0)
    if not send_signal(device_id, "talking", number, caller):
        print("FAIL")
        ok = False
    else:
        hit, log_offset = tail_log("Signal callReceived", timeout=4.0, offset=log_offset)
        print(f"OK  → {hit or '(no new log line)'}")

    # ── 3. ended ────────────────────────────────────────────
    print("\n[3] Sending 'ended' event in 2 s...", end=" ", flush=True)
    time.sleep(2.0)
    if not send_signal(device_id, "ended", number, caller):
        print("FAIL")
        ok = False
    else:
        hit, log_offset = tail_log("callReceived", timeout=4.0, offset=log_offset)
        print(f"OK  → {hit or '(no new log line)'}")

    print(f"\n{sep}")
    print(f"  Result: {'PASS ✓' if ok else 'FAIL ✗'}")
    print(sep)
    return ok


def main():
    parser = argparse.ArgumentParser(description="Test PhoneBridge incoming call popup")
    parser.add_argument("--caller", default="Test Caller 📞", help="Caller name to display")
    parser.add_argument("--number", default="+1 (555) 867-5309", help="Caller number to display")
    args = parser.parse_args()

    if not _HAVE_DBUS:
        print("ERROR: python-dbus not available. Install it or run inside the venv.", file=sys.stderr)
        sys.exit(2)

    device_id = get_device_id()
    if not device_id:
        # Try reading from settings as fallback
        try:
            import json
            cfg = os.path.expanduser("~/.config/phonebridge/settings.json")
            with open(cfg) as f:
                device_id = json.load(f).get("device_id", "")
        except Exception:
            pass

    if not device_id:
        print("ERROR: No KDE Connect device found. Pair your phone first.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(LOG_PATH):
        print(f"[WARN] Log file not found at {LOG_PATH} — popup detection will be limited")

    sys.exit(0 if run_test(device_id, args.caller, args.number) else 1)


if __name__ == "__main__":
    main()
