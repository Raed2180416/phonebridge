#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"

if [[ -n "$MODE" && "$MODE" != "--ci" ]]; then
    echo "Usage: scripts/prepublish_check.sh [--ci]" >&2
    exit 1
fi

cd "$ROOT_DIR"

fail() {
    echo "prepublish: $*" >&2
    exit 1
}

PYTHON_BIN="python3"
PYTEST_CMD=("python3" "-m" "pytest")
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi
if [[ -x "$ROOT_DIR/.venv/bin/pytest" ]]; then
    PYTEST_CMD=("$ROOT_DIR/.venv/bin/pytest")
fi

for required in \
    README.md \
    LICENSE \
    CONTRIBUTING.md \
    docs/ARCHITECTURE.md \
    docs/CONFIGURATION.md \
    docs/PUBLISHING.md \
    docs/PHASE2_KDE_NOTIFICATION_ACTION_PATCH.md \
    tests/README.md \
    pytest.ini \
    .github/workflows/ci.yml
do
    [[ -f "$required" ]] || fail "missing required public file: $required"
done

[[ ! -e "docs/audit" ]] || fail "private audit tree must not exist in the public repo"
if find tests/hardware -maxdepth 1 -type f -name '*.json' | grep -q .; then
    fail "generated hardware JSON reports must not live at tests/hardware root"
fi

mapfile -t PUBLIC_FILES < <(
    rg --files --hidden \
        -g '!.git' \
        -g '!optional_tests/**' \
        -g '!**/__pycache__/**' \
        -g '!SYSTEM_FACTS.md' \
        -g '!tests/hardware/call_mic_report.json'
)

SCAN_FILES=()
for path in "${PUBLIC_FILES[@]}"; do
    if [[ "$path" == "scripts/prepublish_check.sh" ]]; then
        continue
    fi
    SCAN_FILES+=("$path")
done

forbidden_paths=(
    "^docs/PHONEBRIDGE_CRITICAL_AUDIT_REPORT\\.md$"
    "^docs/PHONEBRIDGE_DEEP_DIVE\\.md$"
    "^docs/audit/"
    "^mockui\\.html$"
    "^scripts/nix-guard\\.py$"
    "^scripts/nix-guard\\.service$"
    "^scripts/audit_collect_readonly\\.sh$"
)

for expr in "${forbidden_paths[@]}"; do
    if printf '%s\n' "${PUBLIC_FILES[@]}" | rg -n "$expr" >/dev/null; then
        fail "forbidden tracked artifact matched regex: $expr"
    fi
done

current_home="${HOME:-}"
repo_abs="$ROOT_DIR"

if [[ -n "$current_home" ]]; then
    if rg -n -F "$current_home" "${SCAN_FILES[@]}" >/dev/null; then
        fail "tracked files still contain current HOME path: $current_home"
    fi
fi

if rg -n -F "$repo_abs" "${SCAN_FILES[@]}" >/dev/null; then
    fail "tracked files still contain the absolute repository path"
fi

if rg -n 'LOGNAME:-[A-Za-z0-9._-]+' "${SCAN_FILES[@]}" >/dev/null; then
    fail "tracked files still contain a hardcoded LOGNAME fallback value"
fi

for stale_ref in "PHONEBRIDGE_DEEP_DIVE" "PHONEBRIDGE_CRITICAL_AUDIT_REPORT"; do
    if rg -n -F "$stale_ref" "${SCAN_FILES[@]}" >/dev/null; then
        fail "tracked files still reference removed private docs/artifacts: $stale_ref"
    fi
done

grep -q "NixOS" README.md || fail "README is missing the supported host scope"
grep -q "Hyprland" README.md || fail "README is missing the supported compositor scope"
grep -q "docs/CONFIGURATION.md" README.md || fail "README must link to docs/CONFIGURATION.md"
grep -q "docs/ARCHITECTURE.md" README.md || fail "README must link to docs/ARCHITECTURE.md"

"$PYTHON_BIN" -m compileall -q main.py backend ui tests

"${PYTEST_CMD[@]}" -q -rs tests/unit

bash "$ROOT_DIR/scripts/run_qt_tests.sh"

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT
HOME="$tmp_home" "$PYTHON_BIN" - <<'PY'
from pathlib import Path

import backend.autostart as autostart
from backend import system_integration

root = Path.cwd()
runtime_root, launcher = autostart.publish_runtime(str(root))
assert runtime_root.exists(), runtime_root
assert launcher.exists(), launcher
assert launcher.name == "run-venv-runtime.sh"
assert (runtime_root / "main.py").exists()
ok, desktop_path = system_integration.ensure_desktop_entry(root)
assert ok, desktop_path
desktop_text = Path(desktop_path).read_text(encoding="utf-8")
assert f"Exec={launcher}" in desktop_text
assert f"TryExec={launcher}" in desktop_text
print(f"runtime-publish-smoke-ok {runtime_root}")
PY

HOME="$tmp_home" PHONEBRIDGE_TOGGLE_DRY_RUN=1 PHONEBRIDGE_SKIP_SOCKET=1 bash "$ROOT_DIR/scripts/phonebridge-toggle.sh" >"$tmp_home/toggle.out"
expected_launcher="$tmp_home/.local/share/phonebridge/runtime/current/run-venv-runtime.sh --toggle"
actual_launcher="$(tr -d '\r' <"$tmp_home/toggle.out" | tail -n 1)"
[[ "$actual_launcher" == "$expected_launcher" ]] || fail "toggle launcher mismatch: expected '$expected_launcher' got '$actual_launcher'"

echo "prepublish: ok"
