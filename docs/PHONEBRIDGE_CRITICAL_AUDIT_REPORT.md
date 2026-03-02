# PhoneBridge Deterministic Full-Project Audit and Beginner-Friendly Technical Report

Audit date: 2026-03-02  
Repository: `/home/raed/projects/phonebridge`  
Baseline commit: `c42d5b49b13044c716d66d8ea18547dc16dedbb4`  
Primary evidence run: `docs/audit/evidence/latest` -> `runs/20260301T184642Z`

## Evidence Legend
- `CODE`: direct implementation references (file + line)
- `RUN`: observed command/runtime output in evidence artifacts
- `TEST`: deterministic test outputs
- `HIST`: git/rollback archaeology
- `WEB`: external documentation references (official-first)

## Scoring Rubric
- Severity: `Critical`, `High`, `Medium`, `Low`
- Confidence: `High`, `Medium`, `Low`

## 1. Executive Verdict

Verdict: **PhoneBridge is functionally working on this exact machine, but not cleanly productionized.**

What is strong:
- Core control plane works end-to-end for this setup (ADB control, KDE event bridge, Tailscale status, Syncthing API, UI orchestration).
- Deterministic tests pass (`25 passed`) and static syntax compile passes.
- The controlled safe route trial was executed and restored baseline safely.

What blocks a "clean/correct" verdict:
1. **Hardcoded secrets and machine identifiers are committed in runtime code**.
2. **Syncthing status semantics are contradictory** (`systemctl` inactive while UI logic can show running).
3. **Startup has non-trivial auto-mutations** (auto-enables startup service and writes Hyprland bindings).
4. **README license claim is contradictory** (`LICENSE file not found`).
5. **Repository hygiene issues** (tracked local rollback snapshots and machine-specific artifacts).

Overall rating for current state:
- Functional reliability for this owner setup: **High**
- Portability and cleanliness: **Low-Medium**
- Security/privacy hygiene: **Low**

Primary blockers to resolve first: `PB-001`, `PB-002`, `PB-005` in [docs/audit/REMEDIATION_BACKLOG.md](docs/audit/REMEDIATION_BACKLOG.md).

## 2. Environment Baseline (Exact Setup Assumptions)

### 2.1 Baseline snapshot
- Repo state: dirty with untracked audit/script outputs (`?? docs/audit/`, `?? scripts/`).
- HEAD commit: `c42d5b49b13044c716d66d8ea18547dc16dedbb4`.
- OS/kernel snapshot: Linux NixOS-like environment (redacted host) with Wayland/Hyprland indicators.
- Python/module footprint:
  - Python files: 43
  - Total Python LOC: 14163
  - `backend` LOC: 4945
  - `ui` LOC: 8143
  - `optional_tests` LOC: 688

Evidence:
- `RUN`: `docs/audit/evidence/latest/baseline/repo_state.txt`
- `RUN`: `docs/audit/evidence/latest/baseline/repo_head.txt`
- `RUN`: `docs/audit/evidence/latest/baseline/repo_overview.txt`
- `RUN`: `docs/audit/evidence/latest/baseline/python_loc_summary.txt`
- `RUN`: `docs/audit/evidence/latest/baseline/largest_modules.txt`

### 2.2 Command availability matrix (this machine)
Available: `python3`, `adb`, `scrcpy`, `tailscale`, `syncthing`, `bluetoothctl`, `systemctl`, `wpctl`, `pw-dump`, `busctl`, `ffmpeg`, `wl-copy`, `xdg-open`, `gio`, `hyprctl`, `steam-run`.  
Missing: `pactl`, `ffmpegthumbnailer`, `xclip`.

Evidence:
- `RUN`: `docs/audit/evidence/latest/runtime/command_availability.txt`

### 2.3 Service/process and connectivity snapshot
- `syncthing.service`: `inactive`, `is-enabled`: `linked-runtime`
- `phonebridge.service`: `active`, `enabled`
- Syncthing processes present and REST ping returns 200/pong
- ADB device connected and call state was `idle`
- Tailscale `BackendState` was `Running`

Evidence:
- `RUN`: `docs/audit/evidence/latest/runtime/service_process_state.txt`
- `RUN`: `docs/audit/evidence/latest/runtime/syncthing_ping.txt`
- `RUN`: `docs/audit/evidence/latest/runtime/adb_devices.txt`
- `RUN`: `docs/audit/evidence/latest/runtime/adb_call_state.txt`
- `RUN`: `docs/audit/evidence/latest/runtime/tailscale_status_summary.txt`

### 2.4 Redaction contract used
Redacted classes in collector include Syncthing API key literal, user path fragments, hostnames, IPs, MACs, and selected identifiers.

Evidence:
- `CODE`: `scripts/audit_collect_readonly.sh:36-47`
- `RUN`: `docs/audit/evidence/latest/meta/evidence_contract.md`

## 3. Architecture Link Map

Beginner summary: PhoneBridge is a desktop UI that talks to your phone through **three channels**:
1. KDE Connect D-Bus for events (calls/notifications/clipboard/battery).
2. ADB/scrcpy for direct commands and mirroring/audio routes.
3. Local services (Tailscale/Syncthing/systemd) for network path and sync operations.

### 3.1 Startup flow
```text
main.py
  -> runtime bootstrap checks (PyQt/dbus issues)
  -> optional re-exec via steam-run + PYTHONPATH system site-packages
  -> singleton lock + IPC socket
  -> ensure_system_integration(...)
  -> create QApplication + tray + window
  -> if --background and startup_check_on_login: run startup checker popup
```

Evidence:
- `CODE`: `main.py:57-90`, `main.py:114-177`, `main.py:219`, `main.py:377-383`
- `CODE`: `backend/system_integration.py:117-150`

### 3.2 Event flow
```text
KDE Connect D-Bus signal
  -> ui/window.py DBusSignalBridge
  -> Qt signals to handlers
  -> state updates + page refresh + notification mirror sync
```

Evidence:
- `CODE`: `ui/window.py:72-105`, `ui/window.py:552-669`
- `CODE`: `backend/kdeconnect.py:569-607`
- `CODE`: `backend/notification_mirror.py:1-298`

### 3.3 Control flow (toggle operations)
```text
UI toggle
  -> ToggleActionWorker
  -> backend/connectivity_controller.py
  -> command execution + post-state verification
  -> busy flags in state
  -> UI reconciliation
```

Evidence:
- `CODE`: `ui/pages/network.py:108-299`, `ui/pages/dashboard.py:113-133`, `ui/pages/dashboard.py:595-626`
- `CODE`: `backend/connectivity_controller.py:18-80`, `backend/connectivity_controller.py:109-130`, `backend/connectivity_controller.py:257-270`

### 3.4 State flow
```text
backend/state.py (in-memory pub/sub)
  - call route: call_route_status/reason/backend
  - connectivity op locks: connectivity_ops_busy
  - clipboard history and latest text
  - notifications + revisions
  - call_ui_state and outbound origin suppression metadata
```

Evidence:
- `CODE`: `backend/state.py:1-96`
- `CODE`: `backend/audio_route.py:51-63`, `backend/audio_route.py:532-553`
- `CODE`: `ui/window.py:495-510`, `ui/window.py:552-625`, `ui/window.py:819-834`

## 4. Feature-by-Feature Truth Table

### 4.1 README Claim -> Code Truth Table

Status legend: `Implemented as claimed`, `Partially implemented`, `Contradictory`, `Unverified`.

| ID | README claim | Status | Why | Evidence |
| --- | --- | --- | --- | --- |
| R-01 | Tailscale + Syncthing lifecycle control with status | Partially implemented | Tailscale lifecycle is explicit and verified; Syncthing "running" is API-ping based and can disagree with unit activity | `CODE` `ui/pages/network.py:41-92`, `backend/syncthing.py:46-48`, `backend/syncthing.py:167-178`; `RUN` `runtime/service_process_state.txt`, `runtime/syncthing_ping.txt` |
| R-02 | Bluetooth and Wi-Fi toggles with post-state verification | Implemented as claimed | Controller enforces command + confirmation poll before success | `CODE` `backend/connectivity_controller.py:66-107` |
| R-03 | Connectivity health checks + busy flags | Implemented as claimed | Lock map and state busy flags prevent overlapping operations | `CODE` `backend/connectivity_controller.py:18-51`; `CODE` `ui/pages/network.py:300-313` |
| R-04 | Place calls from PC | Implemented as claimed | Calls page sends ADB `CALL` intent and tracks outbound origin | `CODE` `ui/pages/calls.py:365-390` |
| R-05 | Switch call audio between phone and laptop mid-call | Implemented as claimed | Explicit route toggle with state machine + rollback path | `CODE` `ui/pages/calls.py:536-567`; `CODE` `backend/audio_route.py:556-621` |
| R-06 | Incoming popup with answer/reject/reply SMS/route | Implemented as claimed | Popup has answer/reject/SMS diversion and route controls | `CODE` `ui/components/call_popup.py:595-607`, `ui/components/call_popup.py:1133-1148`, `ui/components/call_popup.py:1209-1223`, `ui/components/call_popup.py:1270-1415` |
| R-07 | Call audio device + volume selection in settings | Implemented as claimed | Settings page writes selections and applies during call sessions | `CODE` `ui/pages/settings.py:110-180`; `CODE` `backend/call_audio.py:134-150` |
| R-08 | Now Playing panel with player selection | Implemented as claimed | Dashboard refresh reads media sessions, allows package preference | `CODE` `ui/pages/dashboard.py:105-110`, `ui/pages/dashboard.py:684-705`; `CODE` `backend/adb_bridge.py:499-557` |
| R-09 | Clipboard sharing with auto-share + history sanitization | Implemented as claimed | Signal handling writes clipboard and sanitizes bounded history | `CODE` `ui/window.py:683-689`, `ui/window.py:819-834`; `CODE` `backend/clipboard_history.py:1-41` |
| R-10 | Notification mirror with swipe dismiss and two-way sync | Implemented as claimed | UI swipe dismisses, backend mirrors and closes phone notification | `CODE` `ui/pages/messages.py:417-540`; `CODE` `backend/notification_mirror.py:173-253` |
| R-11 | SMS compose from laptop | Implemented as claimed | UI compose calls KDE Connect SMS method | `CODE` `ui/pages/messages.py:541-553`; `CODE` `backend/kdeconnect.py:219-238` |
| R-12 | Notification actions/replies forwarded to phone | Implemented as claimed | Reply and action APIs are wired | `CODE` `backend/kdeconnect.py:189-209`; `CODE` `backend/notification_mirror.py:217-249` |
| R-13 | Syncthing progress/pause/resume/path edit UI | Partially implemented | UI supports all controls, but running-state semantics can mislabel service health | `CODE` `ui/pages/sync.py:11-24`, `ui/pages/sync.py:199-204`, `ui/pages/sync.py:260-280`; `RUN` `runtime/service_process_state.txt` |
| R-14 | File transfer via KDE Connect + add auto-sync folders | Implemented as claimed | Send/share and add-folder APIs are present | `CODE` `ui/pages/files.py:393-412`, `ui/pages/files.py:512-533`; `CODE` `backend/kdeconnect.py:277-305`; `CODE` `backend/syncthing.py:120-160` |
| R-15 | Folder management (default/custom, sync toggle, browse) | Implemented as claimed | Folder card model supports default/custom and sync linkage | `CODE` `ui/pages/files.py:57-114`, `ui/pages/files.py:487-560` |
| R-16 | Ring/lock/DND/Wi-Fi/Bluetooth/hotspot controls | Partially implemented | Ring/lock/DND/Wi-Fi/BT implemented; hotspot behavior is inconsistent by page and often opens settings fallback | `CODE` `ui/pages/dashboard.py:719-728`; `CODE` `ui/pages/network.py:428-435`; `CODE` `backend/adb_bridge.py:278-294` |
| R-17 | Mirror + webcam + screenshot + record + rotate + type | Implemented as claimed | Mirror page and ADB bridge expose all actions | `CODE` `ui/pages/mirror.py:97-190`, `ui/pages/mirror.py:419-462`, `ui/pages/mirror.py:626-638`; `CODE` `backend/adb_bridge.py:260-270`, `backend/adb_bridge.py:694-745` |
| R-18 | Optional Bluetooth auto-connect on startup | Implemented as claimed | Auto-connect worker exists and is settings-gated | `CODE` `ui/window.py:789-817`; `CODE` `backend/connectivity_controller.py:93-103` |
| R-19 | Start-on-login toggle can be disabled from settings | Partially implemented | Toggle works, but startup also auto-enables service in system integration path | `CODE` `ui/pages/settings.py:189-301`; `CODE` `backend/system_integration.py:142-150` |
| R-20 | Hyprland keybind for panel toggle | Partially implemented | Toggle bind exists, but managed file also injects unrelated `SUPER+F` browser bind | `CODE` `backend/system_integration.py:90-99` |
| R-21 | NixOS self-healing re-exec (`steam-run`, dbus exposure) | Implemented as claimed | Bootstrap checks detect known runtime signatures and re-exec | `CODE` `main.py:57-90`; `CODE` `run-venv-nix.sh:13-35` |
| R-22 | Appearance/behavior settings are cleanly represented | Partially implemented | Several settings keys are dead/unused, others display-only but look functional | `RUN` `static/settings_reference_counts.txt`; `CODE` `backend/settings_store.py:13-27`; `CODE` `ui/pages/settings.py:32-39` |
| R-23 | Known limit: hardcoded assumptions and external command reliance | Implemented as claimed | Evidence confirms machine-specific defaults and command dependencies | `CODE` `backend/settings_store.py:13-17`; `RUN` `runtime/command_availability.txt` |
| R-24 | License: Apache 2.0 with LICENSE file | Contradictory | README claims LICENSE exists; repo evidence shows missing file | `CODE` `README.md:255-258`; `RUN` `static/license_presence.txt` |

### 4.2 Deep Dive Claim -> Code Truth Table

| ID | Deep Dive claim | Status | Why | Evidence |
| --- | --- | --- | --- | --- |
| D-01 | Layered architecture (entry/UI/state/backend) | Implemented as claimed | Clear module separation exists | `CODE` `main.py`, `ui/window.py`, `backend/state.py`, backend modules |
| D-02 | D-Bus signal bridge runs in background and emits Qt signals | Implemented as claimed | Dedicated bridge class and signal wiring present | `CODE` `ui/window.py:72-105`, `ui/window.py:519-534` |
| D-03 | Connectivity toggles enforce post-state verification | Implemented as claimed | Controller waits for expected state before success | `CODE` `backend/connectivity_controller.py:53-77`, `backend/connectivity_controller.py:89-127` |
| D-04 | Dual ADB transport strategy (USB preferred, wireless keepalive) | Implemented as claimed | Device parser + target resolution + tcpip keepalive present | `CODE` `backend/adb_bridge.py:39-185` |
| D-05 | Call route gating checks profile and mic path | Implemented as claimed | Profile/mic path checks and failed/pending states are explicit | `CODE` `backend/audio_route.py:131-233`, `backend/audio_route.py:596-621` |
| D-06 | Call route restores prior audio session state | Implemented as claimed | Session snapshot and restore logic exists | `CODE` `backend/call_audio.py:92-131`; `CODE` `backend/audio_route.py:65-73`, `backend/audio_route.py:571-586` |
| D-07 | System integration writes are idempotent/best-effort | Implemented as claimed | `_write_if_changed` + exception guards present | `CODE` `backend/system_integration.py:39-50`, `backend/system_integration.py:117-150` |
| D-08 | Startup/keybind automation reduces setup drift | Partially implemented | Automation exists, but includes unrelated bind and unprompted startup enable | `CODE` `backend/system_integration.py:90-99`, `backend/system_integration.py:142-150` |
| D-09 | Optional tests are removable and non-runtime dependencies | Implemented as claimed | Test pack isolated under `optional_tests` | `CODE` `optional_tests/README.md:1-30`; `TEST` `tests/pytest_optional_tests.txt` |
| D-10 | Known limits include command dependency and portability constraints | Implemented as claimed | Runtime evidence confirms missing optional commands and hardcoded defaults | `RUN` `runtime/command_availability.txt`; `CODE` `backend/settings_store.py:13-17` |

## 5. Decision Ledger (What Was Chosen and Why It Works Here)

| Decision | Technical mechanism | Why it works on this exact setup | Tradeoff / Cost | Evidence |
| --- | --- | --- | --- | --- |
| Nix runtime bootstrap with `steam-run` | Re-exec when known runtime errors are detected; inject system site-packages for `dbus` | NixOS split runtime libs and venv packages are bridged | Adds Linux/Nix coupling | `CODE` `main.py:57-90`, `run-venv-nix.sh:27-35` |
| USB-preferred dual ADB transport | Parse devices; prefer USB; keep wireless alive with `adb tcpip` + `adb connect` | Stabilizes command channel with wireless convenience fallback | More complexity and throttling logic | `CODE` `backend/adb_bridge.py:85-173` |
| Consequential toggle policy in controller | Lock operations + verify state before success | Avoids race conditions and false-positive toggles | More code path branching | `CODE` `backend/connectivity_controller.py:18-130` |
| Call route state machine with explicit statuses | `call_pc_active` source + `sync_result` statuses (`pending`, `active`, `failed`) | Gives deterministic UI state under flaky BT behavior | More moving parts and state coupling | `CODE` `backend/audio_route.py:25-33`, `backend/audio_route.py:532-621` |
| BT call route gated by profile and mic-path checks | Require call profile and input node before active route | Prevents fake "connected" states when only media path exists | Slower route activation under hardware lag | `CODE` `backend/audio_route.py:131-233`, `ui/components/call_popup.py:215-248` |
| Syncthing through local REST + systemctl wrappers | REST for folder state/config; systemctl for start/stop | Works in owner environment where local GUI API is reachable | API reachability can diverge from unit status | `CODE` `backend/syncthing.py:46-48`, `backend/syncthing.py:167-178`; `RUN` `runtime/service_process_state.txt`, `runtime/syncthing_ping.txt` |
| Aggressive system integration on app start | Writes icon, desktop entry, Hypr config, and enables autostart | Eliminates manual setup on owner machine | Surprising side effects and user-control concerns | `CODE` `main.py:219`; `CODE` `backend/system_integration.py:117-150` |
| Mobile-data sync protection | Detect mobile network type and pause/resume folders | Reduces unwanted sync over cellular on this workflow | Network-type detection may be imperfect across devices | `CODE` `ui/window.py:700-776` |

## 6. Why Alternatives Failed (History-Backed)

Historical depth is limited (3 commits total), but major subsystem evolution is clear.

### 6.1 Audio routing evolution
- Old behavior (initial commit): simple on/off global scrcpy audio process; no call-intent source model, no pending/failed statuses, no BT profile/mic-path gating.
- New behavior: full route state machine with call/global source arbitration, retries, pending/failed reporting, and restore paths.
- Probable failure mode of old behavior (inference): inability to safely represent call-specific transitions and BT mic-path uncertainty.

Evidence:
- `HIST`: `git show 8d1928b:backend/audio_route.py` (66-line minimal controller)
- `CODE`: `backend/audio_route.py:19-23`, `backend/audio_route.py:556-621`
- `TEST`: `tests/pytest_optional_tests.txt` includes route-state tests

### 6.2 ADB transport evolution
- Old behavior: fixed target (`adb -s <single target>`), no dynamic target resolution and no device-table validation.
- New behavior: parses `adb devices -l`, prefers USB, keeps wireless alive, validates connect success against real device rows.
- Probable failure mode of old behavior (inference): stale wireless sessions and false readiness.

Evidence:
- `HIST`: `git show 8d1928b:backend/adb_bridge.py:17-33`
- `CODE`: `backend/adb_bridge.py:39-185`

### 6.3 Network toggle flow evolution
- Old behavior: page-level toggles with less centralized synchronization.
- New behavior: dedicated `connectivity_controller` with lock discipline and post-state verification.
- Probable old failure mode (inference): overlapping operations causing inconsistent UI state.

Evidence:
- `HIST`: `git show 8d1928b:ui/pages/network.py`
- `CODE`: `backend/connectivity_controller.py:18-51`

### 6.4 System integration automation added
- Added in second commit: automated autostart, desktop/icon, Hyprland binding management.
- Benefit: faster setup for owner environment.
- Risk introduced: silent configuration mutation and unrelated keybind injection.

Evidence:
- `HIST`: `docs/audit/evidence/latest/history/git_log_oneline.txt`
- `HIST`: `docs/audit/evidence/latest/history/git_log_stat.txt`
- `CODE`: `backend/system_integration.py:90-99`, `backend/system_integration.py:142-150`

## 7. Contradictions and Junk Inventory

Findings are ordered by severity.

### F-01 Hardcoded secret and machine identifiers in committed code
- Severity: `Critical`
- Confidence: `High`
- Classification: `Security/privacy contradiction`
- Impact: secret leakage risk, non-portable defaults, and accidental disclosure of personal infrastructure.
- Evidence:
  - `CODE` `backend/syncthing.py:7-13`
  - `CODE` `backend/startup_check.py:25`
  - `CODE` `backend/settings_store.py:13-17`

### F-02 Syncthing service-state contradiction (API ping != systemd active)
- Severity: `High`
- Confidence: `High`
- Classification: `Behavioral contradiction`
- Impact: UI and control logic can claim "running" while unit is inactive; toggles may misreport outcome.
- Evidence:
  - `CODE` `backend/syncthing.py:46-48`, `backend/syncthing.py:167-178`
  - `CODE` `ui/pages/network.py:372-375`
  - `RUN` `runtime/service_process_state.txt`
  - `RUN` `runtime/syncthing_ping.txt`
  - `WEB` `WEB-07`, `WEB-08`

### F-03 Startup auto-mutations without explicit user consent
- Severity: `High`
- Confidence: `High`
- Classification: `Operational cleanliness`
- Impact: app startup can alter desktop integration and service state unexpectedly.
- Evidence:
  - `CODE` `main.py:219`
  - `CODE` `backend/system_integration.py:117-150`
  - `CODE` `backend/autostart.py:52-63`

### F-04 License claim contradicts repository contents
- Severity: `High`
- Confidence: `High`
- Classification: `Documentation contradiction`
- Impact: legal ambiguity for users/contributors.
- Evidence:
  - `CODE` `README.md:255-258`
  - `RUN` `static/license_presence.txt`

### F-05 Repository includes machine-specific tracked artifacts
- Severity: `High`
- Confidence: `High`
- Classification: `Repository hygiene / privacy`
- Impact: host/user/process fingerprints and local rollback debris are versioned.
- Evidence:
  - `RUN` `static/tracked_artifact_probe.txt`
  - `HIST` `history/rollback_snapshot_inventory.txt`
  - `CODE` `.gitignore:1-26` (missing ignores for these artifact classes)

### F-06 Unused/misleading settings keys
- Severity: `Medium`
- Confidence: `High`
- Classification: `Config debt`
- Impact: user-facing settings imply behavior that does not exist.
- Evidence:
  - `RUN` `static/settings_reference_counts.txt` (`adb_target`, `theme_variant`, `surface_alpha_mode`)
  - `CODE` `backend/settings_store.py:13`, `backend/settings_store.py:25-27`

### F-07 Unrelated `SUPER+F` keybind injection in managed Hypr config
- Severity: `Medium`
- Confidence: `High`
- Classification: `Unexpected coupling`
- Impact: app modifies unrelated desktop behavior (`zen` launch) beyond PhoneBridge scope.
- Evidence:
  - `CODE` `backend/system_integration.py:91-95`
  - `HIST` commit message references "toggle/browser bindings" in `git_log_oneline.txt`

### F-08 KDE reachability defaults to true on exception
- Severity: `Medium`
- Confidence: `High`
- Classification: `False-positive health state`
- Impact: transient D-Bus failures can be displayed as healthy.
- Evidence:
  - `CODE` `backend/kdeconnect.py:600-607`

### F-09 Redaction sanitizer is partially hardcoded to one known key
- Severity: `Medium`
- Confidence: `High`
- Classification: `Audit-process fragility`
- Impact: if secret values rotate, redaction may miss new tokens.
- Evidence:
  - `CODE` `scripts/audit_collect_readonly.sh:38`

### F-10 Missing optional binaries impact feature quality
- Severity: `Medium`
- Confidence: `High`
- Classification: `Operational dependency gap`
- Impact: some paths degrade silently (e.g., pactl-dependent branches, xclip fallback).
- Evidence:
  - `RUN` `runtime/command_availability.txt`
  - `CODE` `backend/linux_audio.py:13-19`
  - `CODE` `backend/notification_mirror.py:268-273`

## 8. Security / Privacy / Operational Risks

### 8.1 Security risks
1. Committed local API key and fixed localhost API URL for Syncthing auth path.
2. Device identifiers and private network addresses in defaults can leak topology.
3. Absence of explicit secret-loading policy encourages further hardcoding.

Evidence: `CODE` `backend/syncthing.py:7-13`, `backend/settings_store.py:13-17`.

### 8.2 Privacy risks
1. Tracked artifact files include host/user/process telemetry (`pw_status.json`, rollback snapshots).
2. Audit evidence storage can accumulate machine metadata if not curated.

Evidence: `RUN` `static/tracked_artifact_probe.txt`, `history/rollback_snapshot_inventory.txt`.

### 8.3 Operational risks
1. Health-state mismatches (Syncthing API vs service) can mislead remediation actions.
2. Startup side effects can cause config drift on multi-machine use.
3. Missing optional command dependencies can produce inconsistent behavior by subsystem.

Evidence: `RUN` runtime/state files and `CODE` references in findings above.

## 9. Test and Runtime Evidence

### 9.1 Deterministic tests
- Optional tests: `25 passed in 0.17s`
- Syntax compile: `compileall-ok`

Evidence:
- `TEST` `docs/audit/evidence/latest/tests/pytest_optional_tests.txt`
- `TEST` `docs/audit/evidence/latest/tests/compileall.txt`

### 9.2 Controlled safe active route trial
Preconditions observed:
- ADB connected
- Call state `idle`
- Baseline route sources off

Trial result:
- Route attempt failed with reason: **"Bluetooth call profile unavailable"**.

Restoration result:
- Route sources restored to baseline
- No active route process remained
- Post-state confirmed baseline (`noop` restore)

Evidence:
- `RUN` `docs/audit/evidence/latest/runtime/adb_call_state.txt`
- `RUN` `docs/audit/evidence/latest/runtime/audio_route_snapshot.txt`
- `RUN` `docs/audit/evidence/latest/runtime/route_trial_safe.txt`

### 9.3 Static integrity checks
- Settings usage classification produced deterministic dead-key findings.
- Contradiction probes caught documented mismatch areas.

Evidence:
- `RUN` `docs/audit/evidence/latest/static/settings_reference_counts.txt`
- `RUN` `docs/audit/evidence/latest/static/known_contradiction_probes.txt`

## 10. Prioritized Remediation Backlog

Detailed action plan is in [docs/audit/REMEDIATION_BACKLOG.md](docs/audit/REMEDIATION_BACKLOG.md).

Immediate sequence for highest risk reduction:
1. `PB-001` Externalize secrets and machine defaults.
2. `PB-005` Remove tracked machine artifacts and harden ignore rules.
3. `PB-002` Split Syncthing service state from API reachability.
4. `PB-003` Remove unconsented startup auto-mutations.
5. `PB-004` Remove unrelated Hyprland browser binding.

## 11. Reproducibility Appendix

### 11.1 Collector interface
- Script: `scripts/audit_collect_readonly.sh`
- Usage:
  - `scripts/audit_collect_readonly.sh --quick`
  - `scripts/audit_collect_readonly.sh --full`

Evidence:
- `CODE` `scripts/audit_collect_readonly.sh:4-26`

### 11.2 Deterministic artifact layout
- Run root: `docs/audit/evidence/runs/<RUN_ID>/`
- Latest symlink: `docs/audit/evidence/latest`
- Categories: `baseline/`, `static/`, `runtime/`, `tests/`, `history/`, `meta/`

Evidence:
- `RUN` `docs/audit/evidence/latest/meta/run_info.txt`

### 11.3 Commands executed (high-level)
- Baseline: git state, inventory, LOC map
- Static: TODO/license/settings/command scans
- Runtime: command availability + service/process checks + ADB/Tailscale/Syncthing probes
- Tests: optional pytest + compileall
- History: log/stat/name-only + rollback snapshot inventory
- Controlled route trial with immediate restore

Implementation reference:
- `CODE` `scripts/audit_collect_readonly.sh:75-195`

### 11.4 Redaction map
Current sanitizer redacts:
- specific known Syncthing API key literal
- `/home/<user>` style paths
- selected host/user tokens
- IP addresses and MAC addresses
- 32-char hex IDs

Evidence:
- `CODE` `scripts/audit_collect_readonly.sh:36-47`

### 11.5 Determinism limits (explicit)
- Runtime checks are deterministic for command outputs at collection time, but service/network/device state can change between runs.
- Safe route trial outcome depends on current Bluetooth call profile readiness and phone state.
- Historical analysis is limited by short commit history depth.

## 12. Sources Appendix (Official + Community)

Primary source index: [docs/audit/SOURCE_INDEX.md](docs/audit/SOURCE_INDEX.md)

Key source IDs used in this report:
- Syncthing REST/API semantics: `WEB-01`, `WEB-02`
- Tailscale operator/daemon semantics: `WEB-03`, `WEB-04`
- scrcpy audio behavior and options: `WEB-05`
- Android audio source context: `WEB-06`
- systemd state semantics (`linked-runtime`, ActiveState): `WEB-07`, `WEB-08`

---

## Appendix A — Settings Usage Matrix (from deterministic scan)

| Setting key | Classification | Notes |
| --- | --- | --- |
| `audio_redirect` | used | Global audio route source toggle persisted in settings |
| `auto_bt_connect` | used | Startup/BT toggle auto-connect behavior |
| `bt_call_ready_mode` | used | BT call-ready profile enforcement |
| `call_input_device` | used | Call audio input selection |
| `call_input_volume_pct` | used | Call input volume persistence |
| `call_output_device` | used | Call audio output selection |
| `call_output_volume_pct` | used | Call output volume persistence |
| `clipboard_autoshare` | used | Phone->desktop clipboard auto-share |
| `clipboard_history` | used | Sanitized history storage |
| `close_to_tray` | used | Close behavior |
| `device_id` | used | KDE device selection |
| `device_name` | used | UI + BT matching hints |
| `dnd_active` | used | DND UI state |
| `kde_integration_enabled` | used | D-Bus bridge gating |
| `motion_level` | used | UI motion level |
| `startup_check_on_login` | used | Startup popup behavior |
| `suppress_calls` | used | Incoming call popup policy |
| `sync_on_mobile_data` | used | Sync mobile policy logic |
| `tailscale_force_off` | used | Force-down policy |
| `theme_name` | used | Theme selection |
| `window_opacity` | used | Visual setting |
| `phone_tailscale_ip` | display-only/misleading | shown in UI but not runtime routing source of truth |
| `nixos_tailscale_ip` | display-only/misleading | shown in UI but not runtime routing source of truth |
| `phonesend_dir` | display-only/misleading | editable but not central driver for all file actions |
| `sync_root` | display-only/misleading | editable but default folder constants still hardcoded path roots |
| `adb_target` | dead/unused | no active references outside defaults/docs scans |
| `theme_variant` | dead/unused | no active references |
| `surface_alpha_mode` | dead/unused | no active references |

Evidence:
- `RUN` `docs/audit/evidence/latest/static/settings_reference_counts.txt`

## Appendix B — Side-Effect Map

| Trigger | Side effect | Scope |
| --- | --- | --- |
| App startup (`main.py`) | Calls `ensure_system_integration` | desktop entry/icon/hypr config/autostart enable |
| System integration | Writes desktop file and icon cache updates | user local desktop assets |
| System integration | Writes `~/.config/hypr/phonebridge.conf` and may append include line to `hyprland.conf` | Hyprland config |
| System integration | If autostart disabled, enables user systemd service | systemd user state |
| Window startup | May apply global audio route from settings | runtime audio process state |
| Mobile-data policy timer | Auto-pauses/resumes Syncthing folders | sync folder state |

Evidence:
- `CODE` `main.py:219`
- `CODE` `backend/system_integration.py:53-114`, `backend/system_integration.py:142-150`
- `CODE` `ui/window.py:324-331`, `ui/window.py:700-776`
- `RUN` `docs/audit/evidence/latest/static/side_effect_probe.txt`
