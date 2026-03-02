#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$ROOT_DIR"
DEVICE_ID=""
DO_STATUS=0
DO_REMOVE=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install_kde_phone_commands.sh [options]

Options:
  --device-id <id>        KDE Connect device id (required)
  --project-root <path>   Override project root (default: repo root)
  --status                Show service + commands status after install/remove
  --remove                Remove runcommand config for the device
  -h, --help              Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device-id)
      DEVICE_ID="${2:-}"; shift 2;;
    --project-root)
      PROJECT_ROOT="${2:-}"; shift 2;;
    --status)
      DO_STATUS=1; shift;;
    --remove)
      DO_REMOVE=1; shift;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 1;;
  esac
done

if [[ -z "$DEVICE_ID" ]]; then
  echo "Missing required arg: --device-id" >&2
  usage
  exit 1
fi

for cmd in python3 systemctl kdeconnect-cli; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

restart_kdeconnect() {
  if systemctl --user status kdeconnectd.service --no-pager >/dev/null 2>&1; then
    systemctl --user try-restart kdeconnectd.service >/dev/null 2>&1 || true
    return
  fi
  if command -v kdeconnectd >/dev/null 2>&1; then
    pkill -x kdeconnectd >/dev/null 2>&1 || true
    nohup kdeconnectd >/dev/null 2>&1 &
    sleep 1
  fi
}

DEVICE_DIR="$HOME/.config/kdeconnect/$DEVICE_ID"
DEVICE_CFG="$DEVICE_DIR/config"
RUNCOMMAND_DIR="$DEVICE_DIR/kdeconnect_runcommand"
RUNCOMMAND_CFG="$RUNCOMMAND_DIR/config"
ACTION_SCRIPT="$PROJECT_ROOT/scripts/kde_remote_actions.py"

if [[ ! -d "$DEVICE_DIR" ]]; then
  echo "KDE device folder not found: $DEVICE_DIR" >&2
  exit 1
fi

if [[ ! -f "$ACTION_SCRIPT" ]]; then
  echo "Action script not found: $ACTION_SCRIPT" >&2
  exit 1
fi

if [[ "$DO_REMOVE" -eq 1 ]]; then
  rm -f "$RUNCOMMAND_CFG"
  rmdir "$RUNCOMMAND_DIR" 2>/dev/null || true
  restart_kdeconnect
  kdeconnect-cli --refresh >/dev/null 2>&1 || true
else
  mkdir -p "$RUNCOMMAND_DIR"

  python3 - "$DEVICE_CFG" <<'PY'
import configparser
import os
import sys

cfg_path = sys.argv[1]
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
cfg = configparser.ConfigParser()
cfg.optionxform = str
if os.path.exists(cfg_path):
    cfg.read(cfg_path, encoding="utf-8")
if "Plugins" not in cfg:
    cfg["Plugins"] = {}
cfg["Plugins"]["kdeconnect_runcommandEnabled"] = "true"
with open(cfg_path, "w", encoding="utf-8") as fh:
    cfg.write(fh)
PY

  python3 - "$RUNCOMMAND_CFG" "$ACTION_SCRIPT" <<'PY'
import json
import os
import sys

cfg_path = sys.argv[1]
action_script = sys.argv[2]
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)

command_map = {
    "pb_lock_laptop": {
        "name": "Lock Laptop",
        "command": f"python3 {action_script} lock-laptop",
    },
    "pb_shutdown_laptop": {
        "name": "Shutdown Laptop",
        "command": f"python3 {action_script} shutdown-laptop",
    },
    "pb_logout_laptop": {
        "name": "Logout Laptop",
        "command": f"python3 {action_script} logout-laptop",
    },
    "pb_audio_to_phone": {
        "name": "Audio to Phone",
        "command": f"python3 {action_script} audio-to-phone",
    },
    "pb_audio_to_pc": {
        "name": "Audio to PC",
        "command": f"python3 {action_script} audio-to-pc",
    },
}

payload = json.dumps(command_map, separators=(",", ":"), ensure_ascii=True)
escaped = payload.replace('"', r'\"')
text = f"[General]\ncommands=\"@ByteArray({escaped})\"\n"
with open(cfg_path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY

  restart_kdeconnect
  kdeconnect-cli --refresh >/dev/null 2>&1 || true
fi

if [[ "$DO_STATUS" -eq 1 ]]; then
  if systemctl --user status kdeconnectd.service --no-pager >/dev/null 2>&1; then
    systemctl --user status kdeconnectd.service --no-pager || true
  else
    pgrep -a kdeconnectd || true
  fi
  kdeconnect-cli -d "$DEVICE_ID" --list-commands || true
fi

echo "Done."
echo "Device: $DEVICE_ID"
if [[ "$DO_REMOVE" -eq 1 ]]; then
  echo "Removed: $RUNCOMMAND_CFG"
else
  echo "Config: $DEVICE_CFG"
  echo "Run Commands: $RUNCOMMAND_CFG"
fi
