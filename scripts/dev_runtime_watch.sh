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

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv python: $VENV_PY" >&2
  exit 1
fi

cd "$ROOT_DIR"
exec "$VENV_PY" -m backend.dev_runtime_watch --root "$ROOT_DIR" "$@"
