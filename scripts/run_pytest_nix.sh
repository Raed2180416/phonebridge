#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTEST_CMD=("python3" "-m" "pytest")
if [[ -x "$ROOT_DIR/.venv/bin/pytest" ]]; then
    PYTEST_CMD=("$ROOT_DIR/.venv/bin/pytest")
elif command -v pytest >/dev/null 2>&1; then
    PYTEST_CMD=("pytest")
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
  echo "pytest-nix: could not find a system python interpreter for site-packages lookup" >&2
  exit 1
fi

SYS_SITE="$(
  "$SYSTEM_PY" - <<'PY'
import site
paths = [p for p in site.getsitepackages() if p]
print(paths[0] if paths else "")
PY
)"

if [[ -z "$SYS_SITE" ]]; then
  echo "pytest-nix: failed to resolve system site-packages path" >&2
  exit 1
fi

export PYTHONPATH="$SYS_SITE${PYTHONPATH:+:$PYTHONPATH}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

RUNNER=()
if command -v steam-run >/dev/null 2>&1; then
  RUNNER=("steam-run")
fi

exec "${RUNNER[@]}" "${PYTEST_CMD[@]}" "$@"
