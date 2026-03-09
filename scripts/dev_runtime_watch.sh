#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv python: $VENV_PY" >&2
  exit 1
fi

cd "$ROOT_DIR"
exec "$VENV_PY" -m backend.dev_runtime_watch --root "$ROOT_DIR" "$@"
