#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTEST_WRAPPER="$ROOT_DIR/scripts/run_pytest_nix.sh"

OUT_FILE="$(mktemp)"
trap 'rm -f "$OUT_FILE"' EXIT

set +e
"$PYTEST_WRAPPER" -q -rs tests/qt "$@" 2>&1 | tee "$OUT_FILE"
rc=${PIPESTATUS[0]}
set -e

if [[ $rc -ne 0 ]]; then
    exit "$rc"
fi

if rg -n '(^SKIPPED|\b[0-9]+ skipped\b)' "$OUT_FILE" >/dev/null; then
    echo "qt-tests: skips are treated as failures; resolve the runtime or mark/document them explicitly" >&2
    exit 1
fi

echo "qt-tests: ok"
