#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/audit_collect_readonly.sh [--quick|--full]

Modes:
  --quick   Collect baseline/runtime/history evidence, skip tests and route trial
  --full    Collect everything including tests and a safe single route trial
USAGE
}

MODE="full"
if [[ "${1:-}" == "--quick" ]]; then
  MODE="quick"
elif [[ "${1:-}" == "--full" || -z "${1:-}" ]]; then
  MODE="full"
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
else
  echo "Unknown option: ${1}" >&2
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_BASE="$ROOT_DIR/docs/audit/evidence"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$EVIDENCE_BASE/runs/$RUN_ID"
LATEST_LINK="$EVIDENCE_BASE/latest"

mkdir -p "$RUN_DIR"/{baseline,static,runtime,tests,history,meta}

sanitize_stream() {
  sed -E -f "$ROOT_DIR/scripts/audit_sanitize.sed"
}

run_capture() {
  local outfile="$1"
  shift
  {
    echo "\$ $*"
    "$@"
  } 2>&1 | sanitize_stream > "$outfile"
}

run_capture_sh() {
  local outfile="$1"
  shift
  {
    echo "\$ $*"
    bash -lc "$*"
  } 2>&1 | sanitize_stream > "$outfile"
}

# Meta
{
  echo "run_id=$RUN_ID"
  echo "mode=$MODE"
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "root_dir=${ROOT_DIR/\/home\/${USER}/\/home\/<USER>}"
} > "$RUN_DIR/meta/run_info.txt"

# Baseline
run_capture "$RUN_DIR/baseline/repo_state.txt" git -C "$ROOT_DIR" status --short
run_capture "$RUN_DIR/baseline/repo_head.txt" git -C "$ROOT_DIR" rev-parse HEAD
run_capture_sh "$RUN_DIR/baseline/repo_overview.txt" "cd '$ROOT_DIR' && pwd && date -Iseconds && uname -a"
run_capture_sh "$RUN_DIR/baseline/file_inventory_top300.txt" "cd '$ROOT_DIR' && rg --files --hidden -g '!.git' -g '!docs/audit/evidence/**' | head -n 300"
run_capture_sh "$RUN_DIR/baseline/python_loc_summary.txt" "cd '$ROOT_DIR' && echo 'python_files ' \$(rg --files -g '*.py' | wc -l) && echo 'total_py_loc ' \$(wc -l \$(rg --files -g '*.py') | tail -n1 | awk '{print \$1}') && echo 'backend_py_loc ' \$(wc -l \$(rg --files backend -g '*.py') | tail -n1 | awk '{print \$1}') && echo 'ui_py_loc ' \$(wc -l \$(rg --files ui -g '*.py') | tail -n1 | awk '{print \$1}') && echo 'optional_tests_py_loc ' \$(wc -l \$(rg --files optional_tests -g '*.py') | tail -n1 | awk '{print \$1}')"
run_capture_sh "$RUN_DIR/baseline/largest_modules.txt" "cd '$ROOT_DIR' && for f in backend/*.py ui/pages/*.py ui/components/*.py ui/*.py; do wc -l \"\$f\"; done | sort -nr | head -n 40"

# Static checks
run_capture_sh "$RUN_DIR/static/todo_markers.txt" "cd '$ROOT_DIR' && rg -n 'TODO|FIXME|HACK|XXX|BUG|WIP|work in progress|placeholder|stub' backend ui main.py README.md docs/PHONEBRIDGE_DEEP_DIVE.md optional_tests -S || true"
run_capture_sh "$RUN_DIR/static/license_presence.txt" "cd '$ROOT_DIR' && ls -la LICENSE* 2>/dev/null || echo 'LICENSE file not found'"
run_capture_sh "$RUN_DIR/static/command_usage_map.txt" "cd '$ROOT_DIR' && rg -n 'shutil\\.which\\(\"[^\"]+\"\\)|subprocess\\.(run|Popen)\\(|os\\.system\\(|xdg-open|wpctl|pactl|pw-dump|bluetoothctl|tailscale|syncthing|scrcpy|adb|steam-run|hyprctl' backend ui main.py run-venv-nix.sh optional_tests -S"
run_capture_sh "$RUN_DIR/static/settings_reference_counts.txt" "cd '$ROOT_DIR' && python3 - <<'PY'
import re, pathlib, subprocess
text = pathlib.Path('backend/settings_store.py').read_text()
keys = sorted(set(re.findall(r'\"([a-z_]+)\"\\s*:', text)))
targets = [
    'backend',
    'ui',
    'main.py',
    'README.md',
    'docs/PHONEBRIDGE_DEEP_DIVE.md',
    'run-venv-nix.sh',
    'optional_tests',
]
for k in keys:
    p = subprocess.run(['rg','-F','-n',k,*targets], capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    refs = [ln for ln in lines if 'backend/settings_store.py' not in ln]
    cls = 'dead_unused' if len(refs)==0 else ('display_only_candidate' if all('/ui/pages/settings.py:' in r for r in refs) else 'used')
    print(f'{k}\t{len(refs)}\t{cls}')
PY"
run_capture_sh "$RUN_DIR/static/known_contradiction_probes.txt" "cd '$ROOT_DIR' && rg -n 'Apache License|License|Hotspot|open_hotspot_settings|set_hotspot|adb_target|theme_variant|surface_alpha_mode|phone_tailscale_ip|nixos_tailscale_ip|run-venv-nix.sh' README.md docs/PHONEBRIDGE_DEEP_DIVE.md backend ui main.py optional_tests -S"

# Runtime checks
run_capture_sh "$RUN_DIR/runtime/command_availability.txt" "for c in python3 adb scrcpy tailscale syncthing bluetoothctl systemctl wpctl pactl pw-dump busctl ffmpeg ffmpegthumbnailer wl-copy xclip xdg-open gio hyprctl steam-run; do if command -v \"\$c\" >/dev/null 2>&1; then echo \"\$c: yes (\$(command -v \$c))\"; else echo \"\$c: no\"; fi; done"
run_capture_sh "$RUN_DIR/runtime/service_process_state.txt" "systemctl --user is-active syncthing.service || true; systemctl --user is-enabled syncthing.service || true; systemctl --user is-active phonebridge.service || true; systemctl --user is-enabled phonebridge.service || true; pgrep -a syncthing || true; pgrep -a kdeconnectd || true"
run_capture "$RUN_DIR/runtime/adb_devices.txt" adb devices -l
run_capture_sh "$RUN_DIR/runtime/adb_call_state.txt" "cd '$ROOT_DIR' && python3 - <<'PY'
from backend.adb_bridge import ADBBridge
b = ADBBridge()
print('call_state=', b.get_call_state())
print('is_connected=', b.is_connected())
PY"
run_capture_sh "$RUN_DIR/runtime/tailscale_status_summary.txt" "tailscale status --json | python3 -c \"import json,sys; j=json.load(sys.stdin); out={'BackendState': j.get('BackendState'), 'Self': {'HostName': ((j.get('Self') or {}).get('HostName')), 'TailscaleIPs': ((j.get('Self') or {}).get('TailscaleIPs') or []), 'Online': ((j.get('Self') or {}).get('Online'))}, 'Peers': [{'HostName': (p or {}).get('HostName'), 'OS': (p or {}).get('OS'), 'Online': (p or {}).get('Online'), 'Active': (p or {}).get('Active'), 'TailscaleIPs': ((p or {}).get('TailscaleIPs') or [])} for p in (j.get('Peer') or {}).values()]}; print(json.dumps(out, indent=2))\""
run_capture_sh "$RUN_DIR/runtime/syncthing_ping.txt" "cd '$ROOT_DIR' && SYNCTHING_API_KEY=\$(python3 - <<'PY'
import os
try:
  from backend.settings_store import get
except Exception:
  get = None
key = (os.environ.get('PHONEBRIDGE_SYNCTHING_API_KEY') or '').strip()
if (not key) and callable(get):
  try:
    key = str(get('syncthing_api_key', '') or '').strip()
  except Exception:
    key = ''
print(key)
PY
); if [ -n \"$SYNCTHING_API_KEY\" ]; then curl -s -o /tmp/pb_audit_ping_out -w '%{http_code}' -H \"X-API-Key: $SYNCTHING_API_KEY\" http://127.0.0.1:8384/rest/system/ping; echo; cat /tmp/pb_audit_ping_out || true; else echo 'SKIPPED: syncthing_api_key not configured'; fi"
run_capture_sh "$RUN_DIR/runtime/audio_route_snapshot.txt" "cd '$ROOT_DIR' && python3 - <<'PY'
from backend import audio_route
print('sources=', audio_route.current_sources())
print('is_running=', audio_route.is_running())
print('active_backend=', audio_route.active_backend())
print('call_route_status=', audio_route.state.get('call_route_status'))
print('call_route_reason=', audio_route.state.get('call_route_reason'))
print('call_route_backend=', audio_route.state.get('call_route_backend'))
PY"

# Tests and compile checks
if [[ "$MODE" == "full" ]]; then
  run_capture_sh "$RUN_DIR/tests/pytest_optional_tests.txt" "cd '$ROOT_DIR' && if [ -x .venv/bin/pytest ]; then .venv/bin/pytest -q optional_tests; else python3 -m pytest -q optional_tests; fi"
  run_capture_sh "$RUN_DIR/tests/compileall.txt" "cd '$ROOT_DIR' && if [ -x .venv/bin/python ]; then .venv/bin/python -m compileall -q main.py backend ui optional_tests && echo compileall-ok; else python3 -m compileall -q main.py backend ui optional_tests && echo compileall-ok; fi"
else
  echo "skipped in --quick mode" > "$RUN_DIR/tests/pytest_optional_tests.txt"
  echo "skipped in --quick mode" > "$RUN_DIR/tests/compileall.txt"
fi

# History / archaeology
run_capture "$RUN_DIR/history/git_log_oneline.txt" git -C "$ROOT_DIR" log --oneline --decorate -n 120
run_capture "$RUN_DIR/history/git_log_stat.txt" git -C "$ROOT_DIR" log --stat --reverse --max-count=40 -- .
run_capture_sh "$RUN_DIR/history/git_name_only_recent.txt" "cd '$ROOT_DIR' && git log --name-only --pretty=format:'%h %ad %s' --date=short -n 60"
run_capture_sh "$RUN_DIR/history/rollback_snapshot_inventory.txt" "cd '$ROOT_DIR' && find .rollback_snapshots -maxdepth 4 -type f 2>/dev/null | sort || true"

# Safe single route trial (controlled) -- full mode only
if [[ "$MODE" == "full" ]]; then
  run_capture_sh "$RUN_DIR/runtime/route_trial_safe.txt" "cd '$ROOT_DIR' && python3 - <<'PY'
import json
from backend import audio_route
from backend.adb_bridge import ADBBridge

snapshot = {
    'pre_sources': audio_route.current_sources(),
    'pre_running': bool(audio_route.is_running()),
    'pre_backend': audio_route.active_backend(),
    'pre_call_state': ADBBridge().get_call_state(),
}
print('pre_snapshot=', json.dumps(snapshot, indent=2))

result = None
restore = {'attempted': False, 'ok': None, 'status': None, 'reason': ''}
if snapshot['pre_call_state'] != 'idle':
    print('route_trial=SKIPPED_NON_IDLE_CALL_STATE')
else:
    try:
        audio_route.set_source('call_pc_active', True)
        result = audio_route.sync_result(call_retry_ms=6000, retry_step_ms=300, suspend_ui_global=True)
        print('route_result=', json.dumps({
            'ok': bool(result.ok),
            'status': result.status,
            'mode': result.mode,
            'backend': result.backend,
            'reason': result.reason,
        }, indent=2))
    finally:
        restore['attempted'] = True
        prior = snapshot.get('pre_sources', {}) or {}
        audio_route.set_source('call_pc_active', bool(prior.get('call_pc_active', False)))
        audio_route.set_source('ui_global_toggle', bool(prior.get('ui_global_toggle', False)))
        restored = audio_route.sync_result(call_retry_ms=0, retry_step_ms=300, suspend_ui_global=True)
        restore.update({'ok': bool(restored.ok), 'status': restored.status, 'reason': restored.reason})

post = {
    'post_sources': audio_route.current_sources(),
    'post_running': bool(audio_route.is_running()),
    'post_backend': audio_route.active_backend(),
    'post_call_state': ADBBridge().get_call_state(),
    'restore': restore,
}
print('post_snapshot=', json.dumps(post, indent=2))
PY"
else
  echo "skipped in --quick mode" > "$RUN_DIR/runtime/route_trial_safe.txt"
fi

# Evidence contract and scoring rubric
cat > "$RUN_DIR/meta/evidence_contract.md" <<'MD'
# Evidence Contract

- `CODE`: Direct code reference with file + line mapping
- `RUN`: Observed runtime or command output from this audit run
- `TEST`: Deterministic automated test result
- `HIST`: Git/rollback history evidence
- `WEB`: External source evidence (official docs first; community if official is silent)

# Scoring Rubric

- Severity: `Critical`, `High`, `Medium`, `Low`
- Confidence: `High`, `Medium`, `Low`
MD

# Refresh latest symlink
mkdir -p "$EVIDENCE_BASE/runs"
rm -f "$LATEST_LINK"
ln -s "runs/$RUN_ID" "$LATEST_LINK"

echo "Evidence collection complete"
echo "mode=$MODE"
echo "run_dir=$RUN_DIR"
echo "latest=$LATEST_LINK"
