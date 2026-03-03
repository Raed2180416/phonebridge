#!/usr/bin/env bash
# Fast IPC relay for keybinds — no steam-run/bwrap overhead.
# Sends 'toggle' directly to the running PhoneBridge socket (<50 ms).
# Falls back to a full launch via run-venv-nix.sh if the app isn't running.
set -euo pipefail

SOCK="/run/user/$(id -u)/phonebridge-$(id -u).sock"

if [[ -S "$SOCK" ]]; then
    python3 - "$SOCK" <<'PY'
import socket, sys
sock_path = sys.argv[1]
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.5)
    s.connect(sock_path)
    s.sendall(b"toggle")
    s.close()
    sys.exit(0)
except OSError:
    sys.exit(1)
PY
    exit $?
fi

# App not running — do a full launch (will show the window immediately)
exec "$(dirname "$0")/../run-venv-nix.sh" --toggle
