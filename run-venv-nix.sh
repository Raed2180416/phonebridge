#!/bin/sh
if [ -z "${BASH_VERSION:-}" ]; then
  if [ -x /run/current-system/sw/bin/bash ]; then
    exec /run/current-system/sw/bin/bash "$0" "$@"
  fi
  if command -v bash >/dev/null 2>&1; then
    exec "$(command -v bash)" "$0" "$@"
  fi
  echo "bash is required but was not found." >&2
  exit 127
fi
set -euo pipefail

# Run the project venv on NixOS with foreign-wheel runtime libs + dbus module.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
RUNTIME_LAUNCHER="$ROOT_DIR/run-venv-runtime.sh"

SELF_CHECK=0
if [[ "${1:-}" == "--self-check" ]]; then
  SELF_CHECK=1
  shift
fi

if [[ ! -x "$VENV_PY" ]]; then
  if [[ -x "$RUNTIME_LAUNCHER" ]]; then
    exec "$RUNTIME_LAUNCHER" "$@"
  fi
  echo "Missing venv python: $VENV_PY" >&2
  exit 1
fi

if ! command -v steam-run >/dev/null 2>&1; then
  echo "steam-run is required but not found in PATH." >&2
  exit 1
fi

declare -a PY_CANDIDATES=(
  "/etc/profiles/per-user/${USER}/bin/python"
  "/etc/profiles/per-user/${USER}/bin/python3"
  "/run/current-system/sw/bin/python3"
  "/nix/var/nix/profiles/default/bin/python3"
)

if command -v python3 >/dev/null 2>&1; then
  PY_CANDIDATES+=("$(command -v python3)")
fi
if command -v python >/dev/null 2>&1; then
  PY_CANDIDATES+=("$(command -v python)")
fi

SYSTEM_PY=""
for cand in "${PY_CANDIDATES[@]}"; do
  if [[ -x "$cand" ]]; then
    SYSTEM_PY="$cand"
    break
  fi
done

if [[ -z "$SYSTEM_PY" ]]; then
  {
    echo "Could not find a system python interpreter for dbus site-packages lookup."
    echo "Checked candidates:"
    for cand in "${PY_CANDIDATES[@]}"; do
      echo "  - $cand"
    done
  } >&2
  exit 1
fi

SYS_SITE="$(
  "$SYSTEM_PY" - <<'PY'
import site
print(site.getsitepackages()[0])
PY
)"

export PYTHONPATH="$SYS_SITE${PYTHONPATH:+:$PYTHONPATH}"

if [[ "$SELF_CHECK" -eq 1 ]]; then
  echo "VENV_PY=$VENV_PY"
  echo "SYSTEM_PY=$SYSTEM_PY"
  echo "SYS_SITE=$SYS_SITE"
  echo "PYTHONPATH=$PYTHONPATH"
  exit 0
fi

exec steam-run "$VENV_PY" "$ROOT_DIR/main.py" "$@"
