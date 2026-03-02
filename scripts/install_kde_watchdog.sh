#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$ROOT_DIR"

DEVICE_ID=""
PHONE_IP=""
ADB_TARGET=""
INTERVAL_SEC=30
COOLDOWN_SEC=600
FAIL_THRESHOLD=2
DO_ENABLE=0
DO_DISABLE=0
DO_STATUS=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install_kde_watchdog.sh --device-id <id> --phone-ip <ip> --adb-target <target> [options]

Required:
  --device-id <id>
  --phone-ip <ip>
  --adb-target <target>

Optional:
  --interval-sec <n>     Timer interval seconds (default: 30)
  --cooldown-sec <n>     Wake cooldown seconds (default: 600)
  --fail-threshold <n>   Consecutive fail threshold (default: 2)
  --project-root <path>  Override project root (default: repo root)
  --enable               Enable+start timer after install
  --disable              Disable+stop timer
  --status               Show timer/service status
  -h, --help             Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device-id)
      DEVICE_ID="${2:-}"; shift 2;;
    --phone-ip)
      PHONE_IP="${2:-}"; shift 2;;
    --adb-target)
      ADB_TARGET="${2:-}"; shift 2;;
    --interval-sec)
      INTERVAL_SEC="${2:-}"; shift 2;;
    --cooldown-sec)
      COOLDOWN_SEC="${2:-}"; shift 2;;
    --fail-threshold)
      FAIL_THRESHOLD="${2:-}"; shift 2;;
    --project-root)
      PROJECT_ROOT="${2:-}"; shift 2;;
    --enable)
      DO_ENABLE=1; shift;;
    --disable)
      DO_DISABLE=1; shift;;
    --status)
      DO_STATUS=1; shift;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 1;;
  esac
done

if [[ "$DO_ENABLE" -eq 1 && "$DO_DISABLE" -eq 1 ]]; then
  echo "Cannot use --enable and --disable together" >&2
  exit 1
fi

if ! [[ "$INTERVAL_SEC" =~ ^[0-9]+$ ]] || [[ "$INTERVAL_SEC" -lt 1 ]]; then
  echo "--interval-sec must be a positive integer" >&2
  exit 1
fi
if ! [[ "$COOLDOWN_SEC" =~ ^[0-9]+$ ]] || [[ "$COOLDOWN_SEC" -lt 0 ]]; then
  echo "--cooldown-sec must be a non-negative integer" >&2
  exit 1
fi
if ! [[ "$FAIL_THRESHOLD" =~ ^[0-9]+$ ]] || [[ "$FAIL_THRESHOLD" -lt 1 ]]; then
  echo "--fail-threshold must be a positive integer" >&2
  exit 1
fi

if [[ "$DO_DISABLE" -eq 0 ]]; then
  if [[ -z "$DEVICE_ID" || -z "$PHONE_IP" || -z "$ADB_TARGET" ]]; then
    echo "Missing required args: --device-id, --phone-ip, --adb-target" >&2
    usage
    exit 1
  fi
fi

for cmd in systemctl python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

UNIT_DIR="$HOME/.config/systemd/user"
CFG_DIR="$HOME/.config/phonebridge"
ENV_FILE="$CFG_DIR/kde-watchdog.env"
STATE_DIR="$HOME/.cache/phonebridge/kde-watchdog"
SERVICE_FILE="$UNIT_DIR/phonebridge-kde-watchdog.service"
TIMER_FILE="$UNIT_DIR/phonebridge-kde-watchdog.timer"
SCRIPT_PATH="$PROJECT_ROOT/scripts/kde_watchdog.py"

mkdir -p "$UNIT_DIR" "$CFG_DIR"

if [[ "$DO_DISABLE" -eq 0 ]]; then
  if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "Watchdog script not found: $SCRIPT_PATH" >&2
    exit 1
  fi

  cat > "$ENV_FILE" <<EOF
# Managed by install_kde_watchdog.sh
DEVICE_ID=$DEVICE_ID
PHONE_TAILSCALE_IP=$PHONE_IP
ADB_TARGET=$ADB_TARGET
KDE_APP_PACKAGE=org.kde.kdeconnect_tp
FAIL_THRESHOLD=$FAIL_THRESHOLD
WAKE_COOLDOWN_SEC=$COOLDOWN_SEC
STATE_DIR=$STATE_DIR
EOF

  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PhoneBridge KDE Connect watchdog
After=default.target

[Service]
Type=oneshot
EnvironmentFile=%h/.config/phonebridge/kde-watchdog.env
ExecStart=/usr/bin/env python3 $SCRIPT_PATH
StandardOutput=journal
StandardError=journal
EOF

  cat > "$TIMER_FILE" <<EOF
[Unit]
Description=Run PhoneBridge KDE watchdog periodically

[Timer]
OnBootSec=45s
OnUnitActiveSec=${INTERVAL_SEC}s
AccuracySec=5s
Persistent=true
Unit=phonebridge-kde-watchdog.service

[Install]
WantedBy=timers.target
EOF
fi

systemctl --user daemon-reload

if [[ "$DO_ENABLE" -eq 1 ]]; then
  systemctl --user enable --now phonebridge-kde-watchdog.timer
fi

if [[ "$DO_DISABLE" -eq 1 ]]; then
  systemctl --user disable --now phonebridge-kde-watchdog.timer || true
fi

if [[ "$DO_STATUS" -eq 1 ]]; then
  systemctl --user status phonebridge-kde-watchdog.timer --no-pager || true
  systemctl --user status phonebridge-kde-watchdog.service --no-pager || true
  systemctl --user list-timers --all --no-pager | grep phonebridge-kde-watchdog || true
fi

echo "Done."
echo "Env: $ENV_FILE"
echo "Service: $SERVICE_FILE"
echo "Timer: $TIMER_FILE"
