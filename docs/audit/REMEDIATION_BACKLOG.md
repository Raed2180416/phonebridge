# PhoneBridge Remediation Backlog

Ordering rule: highest severity first, then dependency order, then implementation effort.

## Scoring Rubric
- Severity: `Critical`, `High`, `Medium`, `Low`
- Confidence: `High`, `Medium`, `Low`
- Effort: `S` (<=0.5 day), `M` (1-2 days), `L` (3+ days)

## Dependency Overview
- `PB-001` and `PB-010` should run first because they reduce security/legal exposure.
- `PB-002` depends on `PB-001` because secret/config loading must be centralized before service-state fixes.
- `PB-003`, `PB-004`, and `PB-005` can run in parallel after `PB-002`.
- `PB-006`, `PB-007`, `PB-008`, `PB-009`, `PB-011`, `PB-012` can run after core behavior is stabilized.

## Backlog Items

### PB-001 — Externalize Secrets and Machine-Specific Defaults
- Severity: `Critical`
- Confidence: `High`
- Effort: `M`
- Depends On: none
- Files: `backend/syncthing.py`, `backend/startup_check.py`, `backend/settings_store.py`, `README.md`, `docs/PHONEBRIDGE_DEEP_DIVE.md`
- Steps:
1. Remove hardcoded Syncthing API key and load from settings/env.
2. Replace committed IP/device defaults with safe placeholders.
3. Add migration logic: preserve existing local `settings.json` values.
4. Document new config contract and required setup fields.
- Acceptance checks:
1. `rg -n "fCtXuD2RX3d52R7CMTfbzynGmNrHYFQ5|100\.127\.|a9fe30c209da40d4bddce484a2c4112a" backend main.py ui` returns no hardcoded values.
2. App still starts and reaches Syncthing when values are provided via config.
3. Audit collector output shows redacted placeholders only.

### PB-002 — Fix Syncthing State Semantics (Service vs API)
- Severity: `High`
- Confidence: `High`
- Effort: `M`
- Depends On: `PB-001`
- Files: `backend/syncthing.py`, `backend/connectivity_controller.py`, `ui/pages/network.py`, `ui/pages/dashboard.py`, `ui/pages/sync.py`
- Steps:
1. Split status into two explicit signals: `service_active` and `api_reachable`.
2. Update `set_running()` verification to check unit state and API separately.
3. Update UI labels to avoid calling API reachability "service running".
4. Add explicit handling for `linked-runtime` and inactive unit cases.
- Acceptance checks:
1. Network and Dashboard pages show distinct service/api indicators.
2. Controlled test where API responds but unit inactive is displayed as mixed state, not "running".
3. Toggle behavior no longer reports success when desired unit state is not reached.

### PB-003 — Remove Unconsented Startup Mutations
- Severity: `High`
- Confidence: `High`
- Effort: `M`
- Depends On: `PB-002`
- Files: `main.py`, `backend/system_integration.py`, `backend/autostart.py`, `ui/pages/settings.py`
- Steps:
1. Gate integration writes (desktop entry/icon/hypr config/autostart enable) behind explicit settings toggles.
2. Keep startup checks read-only by default.
3. Preserve idempotent write behavior when user opts in.
- Acceptance checks:
1. Fresh startup with defaults does not mutate `~/.config/hypr` or enable user service.
2. Opt-in toggles perform expected writes and can be reversed.
3. Existing users retain prior behavior only when migration marks explicit consent.

### PB-004 — Remove Unrelated Hyprland Binding (`SUPER+F`)
- Severity: `High`
- Confidence: `High`
- Effort: `S`
- Depends On: `PB-003`
- Files: `backend/system_integration.py`, docs
- Steps:
1. Remove browser binding injection from managed config.
2. Restrict managed binding scope to PhoneBridge controls only.
3. Document exact managed lines in user docs.
- Acceptance checks:
1. Generated `~/.config/hypr/phonebridge.conf` contains only PhoneBridge-specific binds.
2. Existing unrelated user bindings are untouched.

### PB-005 — Repository Hygiene and Sensitive Artifact Cleanup
- Severity: `High`
- Confidence: `High`
- Effort: `S`
- Depends On: none
- Files: `.gitignore`, tracked artifacts (`.rollback_snapshots/**`, `pw_status.json`, `result`, `optional_tests/hardware_call_mic_report.json`)
- Steps:
1. Remove machine-specific artifacts from version control.
2. Expand `.gitignore` for local snapshots, hardware reports, generated status dumps, and audit temp outputs.
3. Keep only deterministic, intentionally curated evidence.
- Acceptance checks:
1. `git ls-files` no longer contains machine fingerprint artifacts.
2. Clean clone has no personal host/user/process data committed.

### PB-006 — Make Nix Wrapper User-Agnostic
- Severity: `Medium`
- Confidence: `High`
- Effort: `S`
- Depends On: `PB-001`
- Files: `run-venv-nix.sh`, `README.md`
- Steps:
1. Remove fallback to hardcoded `/etc/profiles/per-user/raed/bin/python`.
2. Use robust discovery (`$USER`, `command -v python3`, Nix profile probes) with clear error messages.
3. Add a self-check command in docs.
- Acceptance checks:
1. Script succeeds on target machine without username literal.
2. Script failure path includes actionable diagnostics.

### PB-007 — Prune Dead/Misleading Settings Keys
- Severity: `Medium`
- Confidence: `High`
- Effort: `M`
- Depends On: `PB-001`, `PB-002`
- Files: `backend/settings_store.py`, `ui/pages/settings.py`, relevant backend modules
- Steps:
1. Remove dead keys (`adb_target`, `theme_variant`, `surface_alpha_mode`) or rewire them.
2. Reclassify display-only keys clearly in UI labels.
3. Add one-time settings migration to drop obsolete fields.
- Acceptance checks:
1. Settings key inventory has zero dead keys.
2. Settings page does not imply behavior for unused keys.

### PB-008 — Correct KDE Reachability Failure Default
- Severity: `Medium`
- Confidence: `High`
- Effort: `S`
- Depends On: `PB-002`
- Files: `backend/kdeconnect.py`, `ui/pages/network.py`, `ui/pages/dashboard.py`
- Steps:
1. Change `is_reachable()` exception fallback from `True` to `False/Unknown`.
2. Surface "Unknown" state where D-Bus health cannot be evaluated.
- Acceptance checks:
1. Forced D-Bus failure does not render reachable=true.
2. UI state matches real error conditions.

### PB-009 — Harden Audit Redaction Patterns
- Severity: `Medium`
- Confidence: `High`
- Effort: `S`
- Depends On: none
- Files: `scripts/audit_collect_readonly.sh`
- Steps:
1. Replace hardcoded single-value API-key redaction with generic token/secret patterns.
2. Redact variable-length device IDs and hostnames more consistently.
3. Add tests for sanitizer behavior using synthetic fixtures.
- Acceptance checks:
1. Rotating Syncthing API key still gets redacted.
2. New host/usernames are redacted without script edits.

### PB-010 — Improve Dependency Preflight and Degraded UX Paths
- Severity: `Medium`
- Confidence: `High`
- Effort: `M`
- Depends On: `PB-002`
- Files: `backend/linux_audio.py`, `backend/notification_mirror.py`, `ui/pages/mirror.py`, `ui/pages/files.py`, startup diagnostics
- Steps:
1. Add explicit feature capability matrix (audio, thumbnails, clipboard fallback) at runtime.
2. Warn once for missing optional binaries (`pactl`, `xclip`, `ffmpegthumbnailer`) and provide fallback behavior text.
3. Avoid silent no-op behavior when command missing.
- Acceptance checks:
1. Missing dependency scenario produces clear UI diagnostics.
2. Features with fallback continue to work with explicit mode indication.

### PB-011 — Documentation Accuracy Sync
- Severity: `Medium`
- Confidence: `High`
- Effort: `S`
- Depends On: `PB-003`, `PB-004`, `PB-007`
- Files: `README.md`, `docs/PHONEBRIDGE_DEEP_DIVE.md`
- Steps:
1. Fix contradictory claims (license, hotspot behavior wording, startup side effects).
2. Add per-feature "requirements + known failure mode" blocks.
3. Link docs claims to code modules/tests for maintainability.
- Acceptance checks:
1. Docs claim-to-code truth table has no contradictory rows.
2. License section matches repository reality.

### PB-012 — Regression Tests for Critical Behavior Contracts
- Severity: `Medium`
- Confidence: `Medium`
- Effort: `M`
- Depends On: `PB-002`, `PB-003`, `PB-008`
- Files: `optional_tests/`, potential non-optional CI tests
- Steps:
1. Add tests for Syncthing service/api split semantics.
2. Add tests for startup non-mutation defaults.
3. Add tests for KDE reachability exception handling.
4. Add sanitizer unit tests for audit collector.
- Acceptance checks:
1. New deterministic tests fail on old behavior and pass on fixed behavior.
2. Test suite documents what remains hardware-dependent.
