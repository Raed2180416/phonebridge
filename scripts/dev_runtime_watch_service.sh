#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_NAME="phonebridge-dev-runtime-watch.service"
UNIT_PATH="${HOME}/.config/systemd/user/${UNIT_NAME}"
WATCH_SCRIPT="${ROOT_DIR}/scripts/dev_runtime_watch.sh"

usage() {
  cat >&2 <<'EOF'
Usage: scripts/dev_runtime_watch_service.sh <enable|disable|status|logs>
EOF
  exit 1
}

write_unit() {
  mkdir -p "$(dirname "$UNIT_PATH")"
  cat >"$UNIT_PATH" <<EOF
[Unit]
Description=PhoneBridge dev runtime auto-publish watcher
After=default.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
ExecStart=${WATCH_SCRIPT}
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
EOF
}

cmd="${1:-}"
case "$cmd" in
  enable)
    write_unit
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT_NAME"
    ;;
  disable)
    systemctl --user disable --now "$UNIT_NAME" >/dev/null 2>&1 || true
    rm -f "$UNIT_PATH"
    systemctl --user daemon-reload
    ;;
  status)
    systemctl --user status "$UNIT_NAME" --no-pager
    ;;
  logs)
    journalctl --user -u "$UNIT_NAME" -n 100 --no-pager
    ;;
  *)
    usage
    ;;
esac
