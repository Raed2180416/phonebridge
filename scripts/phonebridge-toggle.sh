#!/usr/bin/env bash
# Fast IPC relay for keybinds — no steam-run/bwrap overhead.
# Sends 'toggle' directly to the running PhoneBridge socket (<50 ms).
# Falls back to the installed immutable runtime launcher if the app isn't running.
set -euo pipefail

# Hyprland keybind execution may run with a reduced or unsuitable PATH.
USER_NAME="${USER:-}"
if [[ -z "$USER_NAME" ]]; then
    USER_NAME="${LOGNAME:-}"
fi
if [[ -z "$USER_NAME" ]]; then
    USER_NAME="$(id -un 2>/dev/null || true)"
fi
export PATH="/run/current-system/sw/bin:/etc/profiles/per-user/${USER_NAME}/bin:/nix/var/nix/profiles/default/bin:${PATH:-/usr/bin:/bin}"

UID_NUM="${UID:-}"
if [[ -z "$UID_NUM" ]]; then
    UID_NUM="$(id -u)"
fi
SOCK="/run/user/${UID_NUM}/phonebridge-${UID_NUM}.sock"

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
if [[ -z "$SCRIPT_DIR" || "$SCRIPT_DIR" == "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="."
fi

PY_BIN="$(command -v python3 || true)"
if [[ -z "$PY_BIN" && -x /run/current-system/sw/bin/python3 ]]; then
    PY_BIN="/run/current-system/sw/bin/python3"
fi
if [[ -z "$PY_BIN" ]]; then
    # Last-resort fallback for environments with very limited PATH.
    PY_BIN="python"
fi

if [[ "${PHONEBRIDGE_SKIP_SOCKET:-0}" != "1" && -S "$SOCK" ]]; then
    if "$PY_BIN" - "$SOCK" <<'PY'; then
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
        exit 0
    fi
    # Stale socket or race: remove and continue to full launch fallback.
    rm -f "$SOCK" 2>/dev/null || true
fi

resolve_launcher() {
    local global_launcher="${HOME}/.local/share/phonebridge/runtime/current/run-venv-runtime.sh"
    local local_runtime_launcher="${SCRIPT_DIR}/../run-venv-runtime.sh"
    local repo_launcher="${SCRIPT_DIR}/../run-venv-nix.sh"

    if [[ -x "$global_launcher" ]]; then
        printf '%s\n' "$global_launcher"
        return 0
    fi
    if [[ -x "$local_runtime_launcher" ]]; then
        printf '%s\n' "$local_runtime_launcher"
        return 0
    fi
    if [[ "${PHONEBRIDGE_ALLOW_DEV_FALLBACK:-0}" == "1" && -x "$repo_launcher" ]]; then
        printf '%s\n' "$repo_launcher"
        return 0
    fi
    return 1
}

LAUNCHER="$(resolve_launcher || true)"
if [[ -z "$LAUNCHER" ]]; then
    echo "PhoneBridge toggle could not find an installed runtime launcher." >&2
    echo "Set PHONEBRIDGE_ALLOW_DEV_FALLBACK=1 to allow repo-launcher fallback for development." >&2
    exit 1
fi

if [[ "${PHONEBRIDGE_TOGGLE_DRY_RUN:-0}" == "1" ]]; then
    printf '%s --toggle\n' "$LAUNCHER"
    exit 0
fi

exec "$LAUNCHER" --toggle
