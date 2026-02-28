# PhoneBridge Deep Dive: UI/UX, Architecture, Integrations, and System-Specific Workarounds

## Table of Contents
- [1) Scope + Build Context](#1-scope--build-context)
- [2) System Baseline (OS, desktop stack, runtime)](#2-system-baseline-os-desktop-stack-runtime)
- [3) Architecture Overview](#3-architecture-overview)
- [4) End-to-End Control/Data Flows](#4-end-to-end-controldata-flows)
- [5) UI/UX Feature Inventory (page-by-page)](#5-uiux-feature-inventory-page-by-page)
- [6) Backend Feature Inventory (module-by-module)](#6-backend-feature-inventory-module-by-module)
- [7) Remote Command Architecture (Tailscale + KDE Connect + ADB)](#7-remote-command-architecture-tailscale--kde-connect--adb)
- [8) ADB Transport Strategy (wired/wireless, switching, recovery)](#8-adb-transport-strategy-wiredwireless-switching-recovery)
- [9) Call Audio Routing Internals (Bluetooth profile gating, mic path checks)](#9-call-audio-routing-internals-bluetooth-profile-gating-mic-path-checks)
- [10) Notification, Clipboard, SMS, Calls, Files, Sync Behavior](#10-notification-clipboard-sms-calls-files-sync-behavior)
- [11) System Integration (tray, IPC, autostart, Hyprland keybind)](#11-system-integration-tray-ipc-autostart-hyprland-keybind)
- [12) Hacky Workarounds and Why They Worked on This System](#12-hacky-workarounds-and-why-they-worked-on-this-system)
- [13) Reliability/State Management Patterns](#13-reliabilitystate-management-patterns)
- [14) Test Evidence (optional tests + hardware harness)](#14-test-evidence-optional-tests--hardware-harness)
- [15) Known Limits / Tradeoffs / Failure Modes](#15-known-limits--tradeoffs--failure-modes)
- [16) Appendix: Command Snippets + Redacted Identifiers Map](#16-appendix-command-snippets--redacted-identifiers-map)

## 1) Scope + Build Context
This document is a complete technical record of what PhoneBridge currently implements in this repository, including:
- Every top-level UI page and major UX behavior.
- Every backend integration path currently in code.
- Why specific implementation decisions (including hacky ones) worked on this machine.
- External command dependencies (`adb`, `scrcpy`, `tailscale`, `systemctl`, `bluetoothctl`, `pactl`, `wpctl`, D-Bus).

Primary source of truth entrypoints:
- [main.py](/home/raed/projects/phonebridge/main.py)
- [ui/window.py](/home/raed/projects/phonebridge/ui/window.py)
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)

Language and stack:
- Python 3 application logic.
- PyQt6 UI.
- D-Bus (`dbus-python`, GLib loop) for KDE Connect integration.
- Shell command orchestration for device/system control.

## 2) System Baseline (OS, desktop stack, runtime)
Observed baseline from code and config conventions:
- Linux desktop app (NixOS-targeted reliability assumptions).
- Wayland-friendly runtime behavior (`WAYLAND_DISPLAY` explicitly used for `scrcpy`).
- Hyprland integration path for global toggle binding.
- User-level systemd service for autostart.

Runtime bootstrap behavior:
- Startup checks for common Linux runtime failures (`libGL.so.1`, missing `dbus` module).
- On these failures, process re-execs through `steam-run` with augmented `PYTHONPATH` to system site-packages.
- This logic exists directly in [main.py](/home/raed/projects/phonebridge/main.py) and wrapper script [run-venv-nix.sh](/home/raed/projects/phonebridge/run-venv-nix.sh).

Why it works on this environment:
- NixOS often separates wheel runtime libs from venv expectations.
- `steam-run` bridges foreign wheel runtime dependencies (notably OpenGL libs).
- Injecting system Python site-packages exposes `dbus-python` to venv process.

## 3) Architecture Overview
PhoneBridge is a layered desktop control plane:
- **Entry/lifecycle layer**: app startup, single-instance IPC, tray, main window boot.
- **UI layer**: multipage Qt UI + shared theme/motion/components.
- **State/event layer**: lightweight in-memory pub/sub state bus.
- **Backend integration layer**: ADB/scrcpy, KDE Connect D-Bus, Syncthing REST, Tailscale CLI, Bluetooth/audio tooling.

Core files:
- Entry: [main.py](/home/raed/projects/phonebridge/main.py)
- Window orchestration: [ui/window.py](/home/raed/projects/phonebridge/ui/window.py)
- State bus: [backend/state.py](/home/raed/projects/phonebridge/backend/state.py)
- Persistent settings: [backend/settings_store.py](/home/raed/projects/phonebridge/backend/settings_store.py)

Architectural intent that shows up in code:
- UI logic mostly delegates side effects to backend modules.
- Long-running or blocking operations are moved to `QThread` workers per page.
- Cross-feature state (call route, toasts, notification revision, connectivity op busy flags) is centralized via `state`.

## 4) End-to-End Control/Data Flows
### 4.1 App startup and single-instance behavior
1. `main.py` bootstraps runtime and argument mode (`--background`, `--toggle`).
2. It attempts local IPC to existing instance via `/tmp/phonebridge-<uid>.sock`.
3. If no active instance exists, it starts Qt app, tray icon, and main window.
4. It wires tray actions to window/show/hide/connectivity-check/audio-route actions.

References:
- [main.py](/home/raed/projects/phonebridge/main.py)

### 4.2 D-Bus signal bridge flow
1. Main window starts a GLib D-Bus loop in a background thread.
2. KDE Connect signals (calls, notifications, battery, clipboard) are subscribed.
3. Signal callbacks emit Qt signals into UI handlers.
4. UI updates state and triggers page refreshes/toasts/popup behavior.

References:
- [ui/window.py](/home/raed/projects/phonebridge/ui/window.py)
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)

### 4.3 Connectivity toggle flow
1. Page toggle starts a worker thread.
2. Worker calls `backend/connectivity_controller.py` methods.
3. Controller executes command, then polls observed state until confirmed or timeout.
4. Busy flags are published to state and consumed by UI to disable conflicting controls.

References:
- [ui/pages/network.py](/home/raed/projects/phonebridge/ui/pages/network.py)
- [ui/pages/dashboard.py](/home/raed/projects/phonebridge/ui/pages/dashboard.py)
- [backend/connectivity_controller.py](/home/raed/projects/phonebridge/backend/connectivity_controller.py)

### 4.4 Call route flow
1. Incoming/outgoing call event updates call state.
2. Call popup and calls page coordinate audio route intent.
3. `audio_route.sync_result(...)` determines mode and backend.
4. Bluetooth profile/mic path gating is checked before route is considered active.
5. Session restoration happens when call route ends.

References:
- [ui/components/call_popup.py](/home/raed/projects/phonebridge/ui/components/call_popup.py)
- [ui/pages/calls.py](/home/raed/projects/phonebridge/ui/pages/calls.py)
- [backend/audio_route.py](/home/raed/projects/phonebridge/backend/audio_route.py)
- [backend/call_audio.py](/home/raed/projects/phonebridge/backend/call_audio.py)

## 5) UI/UX Feature Inventory (page-by-page)
For each page: user-visible behavior, code path, dependencies, fallbacks/retries, failure/recovery, and why it worked here.

### 5.1 Dashboard (`ui/pages/dashboard.py`)
User sees:
- Device hero card, status pills (KDE/Tailscale/Syncthing), battery/signal/network stats.
- Quick actions (ring, lock, calls panel, clipboard, DND).
- Now Playing controls and app session switcher.
- Global phone-audio route toggle.

Code path:
- `DashboardRefreshWorker` pulls KDE, ADB, Tailscale, Syncthing status.
- `ToggleActionWorker` executes shared connectivity controller operations.
- DND changes via ADB command wrappers.

Dependencies:
- `adb`, KDE D-Bus via backend wrapper, `tailscale`, Syncthing REST.

Fallback/retry behavior:
- Handles missing/failed reads by returning safe defaults (`None`/falsey states).
- Fallback network type hints through ADB if KDE network type unavailable.

Failure/recovery:
- Toggle worker returns result tuple and UI surfaces warning/success toast.
- UI refresh path keeps control plane visible even when one integration is degraded.

Why this worked on this system:
- Separate worker threads keep UI responsive despite occasional CLI latency.
- Multi-source status retrieval avoids single point of truth for connectivity.

Reference:
- [ui/pages/dashboard.py](/home/raed/projects/phonebridge/ui/pages/dashboard.py)

### 5.2 Messages (`ui/pages/messages.py`)
User sees:
- Live mirrored phone notifications with swipe dismiss and clear-all.
- SMS compose with contact autocomplete.
- Quick actions for notification reply/action handling.

Code path:
- Refreshes notifications from KDE Connect backend and state revision triggers.
- Notification rows support swipe gesture and animated collapse.
- SMS compose sends through KDE Connect `sendSms` wrappers.

Dependencies:
- KDE Connect notifications + SMS D-Bus APIs.
- Desktop notification bridge behavior from backend.

Fallback/retry:
- Notification property reads are resilient with per-field fallback extraction.
- SMS sending attempts multiple signature variants.

Failure/recovery:
- Missing action/reply capability falls back to standard dismiss or no-op.
- Poll timer and signal-based refresh reduce staleness.

Why it worked here:
- Defensive parsing in KDE notification fetch handles plugin variability.
- Local state revision events ensure UI refresh after async changes.

References:
- [ui/pages/messages.py](/home/raed/projects/phonebridge/ui/pages/messages.py)
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)
- [backend/notification_mirror.py](/home/raed/projects/phonebridge/backend/notification_mirror.py)

### 5.3 Calls (`ui/pages/calls.py`)
User sees:
- Dialpad, contact search/load, collapsible recent calls.
- Live controls: end, mute, switch call audio route.
- Route status hints (phone, pending, laptop active, speaker-only, failed).

Code path:
- Outbound call launched via ADB intent.
- Route activation uses worker and `audio_route.sync_result(...)`.
- Contacts and history pulled from ADB content providers.

Dependencies:
- `adb shell am`, `adb content query`, Bluetooth/audio backend for route.

Fallback/retry:
- Route worker retries mic-path activation window.
- If route activation fails, state rolls back to phone route.

Failure/recovery:
- Automatic unmute/reset behavior when call ends/idle transitions detected.
- Outbound call popup suppression uses time-bounded origin tracking.

Why it worked here:
- Clear separation between call intent and call route activation prevented stale route ownership.
- State-driven route status messaging made transient failures understandable.

References:
- [ui/pages/calls.py](/home/raed/projects/phonebridge/ui/pages/calls.py)
- [backend/call_routing.py](/home/raed/projects/phonebridge/backend/call_routing.py)
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)
- [backend/audio_route.py](/home/raed/projects/phonebridge/backend/audio_route.py)

### 5.4 Files (`ui/pages/files.py`)
User sees:
- Folder cards for major phone-sync locations.
- Folder browsing, preview actions, open path, sync toggle, path overrides.
- Send files and share text to phone via KDE Connect.

Code path:
- Default + custom folder model merged from settings.
- Syncthing folder add/remove/update via backend REST wrapper.
- KDE Connect share methods used for send operations.

Dependencies:
- Filesystem, Syncthing REST API, KDE Connect share plugin.

Fallback/retry:
- Folder list truncation with load-more for performance.
- Custom folder IDs generated deterministically if not supplied.

Failure/recovery:
- If sync update call fails, UI exposes retry state.
- Empty/missing folder path is handled with descriptive empty-state text.

Why it worked here:
- Syncthing + local filesystem mapping gave predictable desktop-side view.
- Settings-backed folder overrides persisted environment-specific path layout.

References:
- [ui/pages/files.py](/home/raed/projects/phonebridge/ui/pages/files.py)
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py)
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)

### 5.5 Mirror (`ui/pages/mirror.py`)
User sees:
- Mode picker (screen mirror vs webcam).
- Launch/stop controls, live indicator, audio route toggle.
- Controls for screenshot/record/rotate/type plus webcam capture.

Code path:
- Launches `scrcpy` through ADB bridge with mode-specific flags.
- Syncs global audio preference when mirror mode active.
- Process-state timer maintains UI state consistency.

Dependencies:
- `scrcpy`, `adb`, local filesystem for captures/recordings.

Fallback/retry:
- Process liveness checks gate button states and labels.
- Best-effort process kill and cleanup helpers.

Failure/recovery:
- Missing prerequisites surface status/toast feedback.
- Stop/cleanup logic avoids zombie process buildup.

Why it worked here:
- Explicit process polling avoided stale “live” UI states.
- Mode-specific command construction matched device and desktop behavior.

References:
- [ui/pages/mirror.py](/home/raed/projects/phonebridge/ui/pages/mirror.py)
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)

### 5.6 Sync (`ui/pages/sync.py`)
User sees:
- Folder list with state badge, progress bar, transfer rates.
- Pause/resume folder toggles.
- Inline path edit/save control.

Code path:
- Worker fetches folder/status/rate snapshots from Syncthing wrapper.
- UI updates existing rows or rebuilds when count changes.

Dependencies:
- Syncthing REST endpoints (`/rest/config`, `/rest/db/status`, `/rest/system/connections`).

Fallback/retry:
- If Syncthing not running, explicit service-warning state.
- Failed path updates keep control in retry mode.

Failure/recovery:
- Refresh can be triggered repeatedly with worker guard against overlap.

Why it worked here:
- Syncthing exposes enough config/state detail to drive precise per-folder UI.

References:
- [ui/pages/sync.py](/home/raed/projects/phonebridge/ui/pages/sync.py)
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py)

### 5.7 Network (`ui/pages/network.py`)
User sees:
- Tailscale mesh summary + peer list.
- Toggles for Tailscale, KDE Connect, Wi-Fi, Bluetooth, Syncthing, hotspot launcher.

Code path:
- Refresh worker composes combined network payload.
- Toggle worker delegates to `connectivity_controller` for consequential operations.

Dependencies:
- `tailscale`, `adb`, `systemctl --user`, KDE D-Bus reachability checks.

Fallback/retry:
- Toggle operations verify resulting state post-command.
- Operation locks prevent concurrent conflicting toggles.

Failure/recovery:
- On Tailscale operator-permission error, command hint is copied to clipboard.
- UI refresh reconciles true current states after any failure.

Why it worked here:
- “Command succeeded” is not trusted; post-check verification is first-class.
- Busy flags and lock discipline reduce race conditions.

References:
- [ui/pages/network.py](/home/raed/projects/phonebridge/ui/pages/network.py)
- [backend/connectivity_controller.py](/home/raed/projects/phonebridge/backend/connectivity_controller.py)
- [backend/tailscale.py](/home/raed/projects/phonebridge/backend/tailscale.py)

### 5.8 Settings (`ui/pages/settings.py`)
User sees:
- Device identity and network metadata.
- Behavior toggles (call popups, clipboard auto-share, BT auto-connect, sync-on-mobile-data).
- Call audio device + volume controls.
- System controls (start on login, startup check, close-to-tray).
- Appearance theme controls and about actions.

Code path:
- Reads/writes persistent values via settings store.
- Start-on-login toggle calls backend autostart module.
- Call audio controls apply live when call route is active.

Dependencies:
- settings JSON, `systemctl --user`, Linux audio tooling.

Fallback/retry:
- Autostart toggle always reconciles with actual service enabled state.
- Audio controls gracefully handle unavailable device lists.

Failure/recovery:
- UI toasts communicate mismatch between requested and actual state.

Why it worked here:
- Strong persistence model and explicit state reconciliation reduced config drift.

References:
- [ui/pages/settings.py](/home/raed/projects/phonebridge/ui/pages/settings.py)
- [backend/settings_store.py](/home/raed/projects/phonebridge/backend/settings_store.py)
- [backend/autostart.py](/home/raed/projects/phonebridge/backend/autostart.py)
- [backend/call_audio.py](/home/raed/projects/phonebridge/backend/call_audio.py)

## 6) Backend Feature Inventory (module-by-module)
### 6.1 Core orchestration
- [backend/state.py](/home/raed/projects/phonebridge/backend/state.py): in-memory pub/sub with Qt-safe dispatch when callbacks run from non-UI thread.
- [backend/settings_store.py](/home/raed/projects/phonebridge/backend/settings_store.py): persistent JSON config under `~/.config/phonebridge/settings.json`.
- [backend/logger.py](/home/raed/projects/phonebridge/backend/logger.py): structured log initialization.

### 6.2 Device and transport integrations
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py): ADB command layer, target resolution, Wi-Fi/BT toggles, media/call controls, screen features.
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py): D-Bus wrapper over battery, notifications, clipboard, telephony, SMS, file/share plugins.
- [backend/tailscale.py](/home/raed/projects/phonebridge/backend/tailscale.py): `tailscale` CLI wrapper with error classification.
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py): Syncthing REST configuration/state operations.
- [backend/bluetooth_manager.py](/home/raed/projects/phonebridge/backend/bluetooth_manager.py): bluetoothctl/busctl/wpctl helpers for connect/disconnect/profile operations.

### 6.3 Control/policy layers
- [backend/connectivity_controller.py](/home/raed/projects/phonebridge/backend/connectivity_controller.py): lock-guarded consequential toggles with post-state verification.
- [backend/audio_route.py](/home/raed/projects/phonebridge/backend/audio_route.py): global audio route state machine (`audio_output` vs call `audio` modes).
- [backend/call_audio.py](/home/raed/projects/phonebridge/backend/call_audio.py): call-session device/volume apply + session snapshot/restore.
- [backend/call_routing.py](/home/raed/projects/phonebridge/backend/call_routing.py): call event normalization and outbound popup suppression helpers.

### 6.4 UX/OS integration helpers
- [backend/notification_mirror.py](/home/raed/projects/phonebridge/backend/notification_mirror.py): phone notification to desktop mirror with 2-way sync.
- [backend/startup_check.py](/home/raed/projects/phonebridge/backend/startup_check.py): startup connectivity popup and concurrent checks.
- [backend/system_integration.py](/home/raed/projects/phonebridge/backend/system_integration.py): desktop entry, icon, Hyprland keybind setup.
- [backend/autostart.py](/home/raed/projects/phonebridge/backend/autostart.py): user systemd service enable/disable.
- [backend/clipboard_history.py](/home/raed/projects/phonebridge/backend/clipboard_history.py): history sanitization.
- [backend/ui_feedback.py](/home/raed/projects/phonebridge/backend/ui_feedback.py): toast queue entrypoint.

## 7) Remote Command Architecture (Tailscale + KDE Connect + ADB)
### User-visible outcome
Phone can be controlled remotely from laptop UI, including command execution paths for connectivity, calls, media, clipboard, files, and notifications.

### Actual architecture
- **Reachability plane**: Tailscale mesh provides stable private network path for remote ADB target.
- **Event plane**: KDE Connect D-Bus streams call/notification/clipboard/battery events.
- **Command/control plane**: ADB executes explicit shell/intents/content queries and `scrcpy` sessions.

References:
- [backend/tailscale.py](/home/raed/projects/phonebridge/backend/tailscale.py)
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)

Fallback/recovery strategy:
- KDE Connect unavailable: app still drives core controls through ADB.
- ADB wireless degraded: USB transport preferred automatically when present.
- Tailscale state mismatch: controller enforces desired state and re-verifies.

Why it worked on this machine:
- The architecture intentionally avoids hard dependence on one channel.
- Event and command paths are decoupled, so transient one-plane failures do not fully collapse control.

## 8) ADB Transport Strategy (wired/wireless, switching, recovery)
### Strategy implemented
- Parse `adb devices -l` and classify transports (`usb` vs `wireless`).
- Prefer connected USB serial when available.
- Keep wireless target alive from USB using `adb tcpip <port>` plus `adb connect <target>`.
- Validate `adb connect` claims by re-reading device table and requiring true `device` state.

Key code:
- `_parse_adb_devices`, `_pick_connected_target`, `_resolve_target`, `_ensure_wireless_keepalive_from_usb`, `_connect_wireless` in [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py).

Fallbacks/retries:
- Throttled connect attempts to avoid spam.
- Last-chance forced device table refresh before declaring no target.

Failure/recovery:
- If no target resolved, command is skipped with explicit warning, not blind failure.
- UI can continue rendering stale-safe states while connectivity recovers.

Why it worked here:
- Dual-transport persistence avoided fragile “wireless only” assumptions.
- Real-state validation prevented false-positive connect states.

## 9) Call Audio Routing Internals (Bluetooth profile gating, mic path checks)
### User-visible behavior
- Calls can stay on phone audio, or be routed to laptop path with explicit status transitions.
- Route status clearly reports `pending`, `active`, `failed`, and backend type.

### Internal model
`audio_route` maintains source intents:
- `ui_global_toggle` for general media redirect.
- `call_pc_active` for active call route intent.

Desired mode resolution:
- Active call intent -> `audio` mode.
- Else global toggle -> `audio_output` mode.
- Else no route.

References:
- [backend/audio_route.py](/home/raed/projects/phonebridge/backend/audio_route.py)

### Bluetooth gating logic
Before call route is considered active:
- Check BT call profile presence.
- Check BT call mic input path (`bluez_input`, handsfree/headset source visibility).
- Retry mic path within configured timeout.
- On failure, rollback call route source and publish failed status.

Supporting modules:
- [backend/bluetooth_manager.py](/home/raed/projects/phonebridge/backend/bluetooth_manager.py)
- [backend/linux_audio.py](/home/raed/projects/phonebridge/backend/linux_audio.py)
- [backend/call_audio.py](/home/raed/projects/phonebridge/backend/call_audio.py)
- [ui/components/call_popup.py](/home/raed/projects/phonebridge/ui/components/call_popup.py)

### Session restore behavior
- On leaving call route, previous default sink/source and volume levels are restored from snapshot.

Why it worked here:
- The design treated “profile visible” and “usable call mic path active” as separate checks.
- Prevented route from being marked active when only A2DP-like media path existed.

## 10) Notification, Clipboard, SMS, Calls, Files, Sync Behavior
### 10.1 Notifications
- Source: KDE Connect notifications plugin.
- Mirror: Desktop notifications with per-notification mapping.
- Two-way sync: Desktop dismiss triggers phone dismiss callback.
- Action/reply handling forwarded back to phone app when available.

References:
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)
- [backend/notification_mirror.py](/home/raed/projects/phonebridge/backend/notification_mirror.py)
- [ui/pages/messages.py](/home/raed/projects/phonebridge/ui/pages/messages.py)

### 10.2 Clipboard
- Phone clipboard events arrive via KDE Connect signal bridge.
- Optional auto-share to desktop clipboard controlled by settings.
- Clipboard history is sanitized and persisted.

References:
- [ui/window.py](/home/raed/projects/phonebridge/ui/window.py)
- [backend/clipboard_history.py](/home/raed/projects/phonebridge/backend/clipboard_history.py)

### 10.3 SMS
- SMS send attempts multiple D-Bus invocation signatures for compatibility.
- Conversation request APIs are exposed in backend for future/full-thread sync usage.

References:
- [backend/kdeconnect.py](/home/raed/projects/phonebridge/backend/kdeconnect.py)
- [ui/pages/messages.py](/home/raed/projects/phonebridge/ui/pages/messages.py)

### 10.4 Calls
- Incoming call signal path: KDE Connect telephony signal -> window handler -> call popup + calls page.
- Outgoing path: calls page places intent via ADB and sets outbound origin state.
- Popup suppression: outbound-origin active window prevents duplicate incoming-style popup.

References:
- [ui/window.py](/home/raed/projects/phonebridge/ui/window.py)
- [backend/call_routing.py](/home/raed/projects/phonebridge/backend/call_routing.py)
- [ui/components/call_popup.py](/home/raed/projects/phonebridge/ui/components/call_popup.py)

### 10.5 Files
- KDE share/send and Syncthing-managed folder model are combined in one UX.
- Default and custom folder cards allow mixed managed/unmanaged file paths.

References:
- [ui/pages/files.py](/home/raed/projects/phonebridge/ui/pages/files.py)
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py)

### 10.6 Sync
- Periodic refresh of folder states and transfer totals.
- Pause/resume and path editing routed through Syncthing config API.

References:
- [ui/pages/sync.py](/home/raed/projects/phonebridge/ui/pages/sync.py)
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py)

## 11) System Integration (tray, IPC, autostart, Hyprland keybind)
### Tray and IPC
- UNIX socket IPC enables `--toggle`, `--show`, background noop behavior.
- Tray supports open/check connectivity/route audio/quit.

Reference:
- [main.py](/home/raed/projects/phonebridge/main.py)

### Autostart
- User-level systemd service unit generated at runtime.
- `ExecStart` uses `run-venv-nix.sh --background`.

References:
- [backend/autostart.py](/home/raed/projects/phonebridge/backend/autostart.py)
- [run-venv-nix.sh](/home/raed/projects/phonebridge/run-venv-nix.sh)

### Desktop and keybind integration
- App icon + desktop entry written under `~/.local/share/...`.
- Hyprland `SUPER+P` toggle binding is injected via `~/.config/hypr/phonebridge.conf` include.

Reference:
- [backend/system_integration.py](/home/raed/projects/phonebridge/backend/system_integration.py)

Failure/recovery behavior:
- Integration functions are best-effort and log exceptions; app continues even if one integration step fails.

Why it worked here:
- System integration is idempotent (`write_if_changed`) and tolerant of missing optional subsystems.

## 12) Hacky Workarounds and Why They Worked on This System
### 12.1 NixOS runtime bootstrap through `steam-run`
Problem:
- Qt wheel runtime dependencies (`libGL.so.1`) and `dbus` import mismatch between venv and system packages.

Workaround:
- Detect known runtime failure signatures and re-exec through `steam-run` with system site-packages appended to `PYTHONPATH`.

Why it worked:
- `steam-run` provides required runtime libs for foreign wheels.
- System Python site package discovery injects `dbus-python` availability into app process.

References:
- [main.py](/home/raed/projects/phonebridge/main.py)
- [run-venv-nix.sh](/home/raed/projects/phonebridge/run-venv-nix.sh)

### 12.2 Dual ADB transport keepalive (USB + wireless)
Problem:
- Wireless ADB links can stale out; USB and wireless can disagree in reported readiness.

Workaround:
- Prefer USB when present but periodically re-enable TCP/IP from USB path and reconnect wireless target.
- Validate `adb connect` by checking actual device row state.

Why it worked:
- USB acts as reliable anchor, wireless remains warm fallback, and state validation avoids ghost-ready state.

Reference:
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)

### 12.3 Wi-Fi/Bluetooth toggles with dual command paths + confirmation polling
Problem:
- Android builds vary in support/reliability of `cmd` vs `svc` subcommands.

Workaround:
- Issue preferred command path, fallback to alternate command path.
- Poll readback state with timeout to confirm actual hardware/radio state.

Why it worked:
- Does not trust one command family and always verifies final state.

References:
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)
- [backend/connectivity_controller.py](/home/raed/projects/phonebridge/backend/connectivity_controller.py)

### 12.4 Bluetooth call routing gated by real mic-path detection
Problem:
- BT connection/profile can be present while usable call mic path is missing.

Workaround:
- Separate checks for profile presence and active input node path; delay/fail route if mic path not detected.
- Roll back route state on timeout.

Why it worked:
- Prevents false “connected” state and ensures call route reflects actual usable I/O.

References:
- [backend/audio_route.py](/home/raed/projects/phonebridge/backend/audio_route.py)
- [backend/linux_audio.py](/home/raed/projects/phonebridge/backend/linux_audio.py)
- [ui/components/call_popup.py](/home/raed/projects/phonebridge/ui/components/call_popup.py)

### 12.5 Notification mirror two-way dismissal sync
Problem:
- Desktop and phone notification states diverge if only one side is updated.

Workaround:
- Maintain phone<->desktop ID maps and intercept desktop close/action/reply signals to propagate back.

Why it worked:
- Mapping table guarantees reverse lookup; close-action guard avoids feedback-loop closures.

Reference:
- [backend/notification_mirror.py](/home/raed/projects/phonebridge/backend/notification_mirror.py)

### 12.6 Systemd user service + Hyprland binding automation
Problem:
- Manual setup of startup and global toggle keybind is easy to drift.

Workaround:
- Auto-generate service unit, icon, desktop entry, Hyprland include config at startup.

Why it worked:
- Idempotent writes and best-effort reload reduce setup friction while preserving user control.

References:
- [backend/autostart.py](/home/raed/projects/phonebridge/backend/autostart.py)
- [backend/system_integration.py](/home/raed/projects/phonebridge/backend/system_integration.py)

## 13) Reliability/State Management Patterns
### 13.1 In-memory canonical UI state
- Global keys for notifications, call state, route status, connectivity busy flags, toasts.
- Subscribers are Qt-thread-safe via queued dispatch.

Reference:
- [backend/state.py](/home/raed/projects/phonebridge/backend/state.py)

### 13.2 Concurrency controls around consequential operations
- Operation-scoped locks for wifi/bluetooth/tailscale/kde/syncthing toggles.
- UI busy indicators derived from state map.

Reference:
- [backend/connectivity_controller.py](/home/raed/projects/phonebridge/backend/connectivity_controller.py)

### 13.3 Worker-based page refreshes
- Each major page uses `QThread` workers for network/CLI operations.
- Avoids UI freeze and keeps stale-safe presentation when dependencies fail.

References:
- [ui/pages/dashboard.py](/home/raed/projects/phonebridge/ui/pages/dashboard.py)
- [ui/pages/network.py](/home/raed/projects/phonebridge/ui/pages/network.py)
- [ui/pages/sync.py](/home/raed/projects/phonebridge/ui/pages/sync.py)
- [ui/pages/calls.py](/home/raed/projects/phonebridge/ui/pages/calls.py)

### 13.4 Defensive external command strategy
- Most command wrappers return `(ok, output)` rather than raising.
- Callers apply fallback branches and verify resulting state.

References:
- [backend/adb_bridge.py](/home/raed/projects/phonebridge/backend/adb_bridge.py)
- [backend/tailscale.py](/home/raed/projects/phonebridge/backend/tailscale.py)
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py)

## 14) Test Evidence (optional tests + hardware harness)
Optional test pack exists under `optional_tests/` and is intentionally removable.

Evidence sources:
- [optional_tests/README.md](/home/raed/projects/phonebridge/optional_tests/README.md)
- [optional_tests/test_audio_route_state_machine.py](/home/raed/projects/phonebridge/optional_tests/test_audio_route_state_machine.py)
- [optional_tests/test_outbound_popup_suppression.py](/home/raed/projects/phonebridge/optional_tests/test_outbound_popup_suppression.py)
- [optional_tests/test_call_mic_activation_transition.py](/home/raed/projects/phonebridge/optional_tests/test_call_mic_activation_transition.py)
- [optional_tests/test_bluetooth_call_route_switch.py](/home/raed/projects/phonebridge/optional_tests/test_bluetooth_call_route_switch.py)
- [optional_tests/test_adb_call_state.py](/home/raed/projects/phonebridge/optional_tests/test_adb_call_state.py)
- [optional_tests/hardware_call_mic_harness.py](/home/raed/projects/phonebridge/optional_tests/hardware_call_mic_harness.py)
- [optional_tests/hardware_call_mic_report.json](/home/raed/projects/phonebridge/optional_tests/hardware_call_mic_report.json)

What is validated:
- Audio route state transitions and priority rules.
- Popup suppression for outbound-initiated calls.
- BT call route release fallback behavior.
- ADB call-state parser behavior.
- Hardware-side call mic path checks (harness script).

## 15) Known Limits / Tradeoffs / Failure Modes
1. Hard-coded environment assumptions exist in defaults and convenience constants; portability requires per-system settings tuning.
2. CLI-driven control paths depend on external tools being installed and executable in PATH.
3. D-Bus integrations depend on KDE Connect daemon/plugin availability and session bus health.
4. Bluetooth call route activation is inherently flaky across adapters/profiles; gating logic improves correctness but can delay route activation.
5. Syncthing integration depends on API availability and valid local key config.
6. Some controls are best-effort and intentionally non-fatal so app remains operational even with partial subsystem failure.

References:
- [backend/settings_store.py](/home/raed/projects/phonebridge/backend/settings_store.py)
- [backend/connectivity_controller.py](/home/raed/projects/phonebridge/backend/connectivity_controller.py)
- [backend/audio_route.py](/home/raed/projects/phonebridge/backend/audio_route.py)

## 16) Appendix: Command Snippets + Redacted Identifiers Map
### 16.1 Command snippets used by architecture
```bash
# Launch app
python3 main.py

# NixOS wrapper launch
./run-venv-nix.sh --background

# ADB transport
adb devices -l
adb tcpip 5555
adb connect <PHONE_TAILSCALE_IP>:5555

# Radio toggles
adb -s <TARGET> shell cmd wifi set-wifi-enabled enabled
adb -s <TARGET> shell svc wifi enable
adb -s <TARGET> shell svc bluetooth enable
adb -s <TARGET> shell cmd bluetooth_manager enable

# Call/media control
adb -s <TARGET> shell input keyevent KEYCODE_HEADSETHOOK
adb -s <TARGET> shell input keyevent KEYCODE_ENDCALL
scrcpy --serial <TARGET> --audio-source=output --no-video --no-window

# Tailscale
tailscale status --json
tailscale up
tailscale down

# Syncthing service
systemctl --user start syncthing.service
systemctl --user stop syncthing.service

# Bluetooth and audio diagnostics
bluetoothctl show
bluetoothctl devices Paired
wpctl status
pactl list short sinks
pactl list short sources
```

### 16.2 Redacted identifiers map
The following identifier classes are intentionally redacted in this document:
- `API_KEY` values used for local Syncthing API auth in code.
- Long device IDs and any personally identifying tailscale node metadata.
- Exact personal host-specific paths where not required.

Placeholder format used:
- `<PHONE_TAILSCALE_IP>`
- `<NIXOS_TAILSCALE_IP>`
- `<DEVICE_ID_REDACTED>`
- `<SYNCTHING_API_KEY_REDACTED>`

Where values are sourced in code/settings:
- [backend/settings_store.py](/home/raed/projects/phonebridge/backend/settings_store.py)
- [backend/syncthing.py](/home/raed/projects/phonebridge/backend/syncthing.py)
- [backend/startup_check.py](/home/raed/projects/phonebridge/backend/startup_check.py)

---
This deep-dive is intended as the canonical technical reference for this repository’s current implementation state.
