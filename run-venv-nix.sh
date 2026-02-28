#!/usr/bin/env bash
set -euo pipefail

# Run the project venv on NixOS with foreign-wheel runtime libs + dbus module.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv python: $VENV_PY" >&2
  exit 1
fi

if ! command -v steam-run >/dev/null 2>&1; then
  echo "steam-run is required but not found in PATH." >&2
  exit 1
fi

SYSTEM_PY="/etc/profiles/per-user/${USER}/bin/python"
if [[ ! -x "$SYSTEM_PY" ]]; then
  SYSTEM_PY="/etc/profiles/per-user/raed/bin/python"
fi
if [[ ! -x "$SYSTEM_PY" ]]; then
  echo "Could not find system python for dbus site-packages lookup." >&2
  exit 1
fi

SYS_SITE="$(
  "$SYSTEM_PY" - <<'PY'
import site
print(site.getsitepackages()[0])
PY
)"

export PYTHONPATH="$SYS_SITE${PYTHONPATH:+:$PYTHONPATH}"
exec steam-run "$VENV_PY" "$ROOT_DIR/main.py" "$@"
