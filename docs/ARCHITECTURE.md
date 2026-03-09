# Architecture

PhoneBridge is a source-run Qt6 desktop app that orchestrates four existing systems instead of replacing them:

- KDE Connect for notifications, SMS, clipboard, telephony metadata, and file send.
- ADB and scrcpy for direct Android control, media inspection, and recovery paths.
- Syncthing for folder sync and sync-state inspection.
- Tailscale for mesh reachability and device discovery.

## Supported Scope

Public v1 is intentionally narrow:

- Host OS: NixOS
- Compositor: Hyprland
- Desktop integration: systemd user services
- Phone bridge: KDE Connect plus ADB

Anything outside that matrix should be treated as experimental.

## Runtime Layout

- `main.py`
  Handles singleton startup, runtime bootstrap through `steam-run` when needed, and window launch/toggle behavior.
- `ui/window.py`
  Owns the top-level shell window, page stack, tray integration, and wires controllers to the UI.
- `ui/runtime_controllers.py`
  Owns timer-driven runtime concerns that should not live directly in the window: call polling, health probes, connectivity policy refresh, and notification startup priming.
- `backend/state.py`
  Shared in-memory state bus with thread-safe updates, unsubscribe support, and Qt-safe callback dispatch.
- `backend/settings_store.py`
  Persistent JSON settings store with atomic writes, environment overrides, and dead-key migration.

## Runtime Boundaries

The app is deliberately split into three layers:

1. UI pages and widgets
   Render state and send user intents. They should not own transport logic.
2. Runtime controllers
   Coordinate timers, background work, and lifecycle-sensitive behavior.
3. Backends
   Encapsulate transport and platform integration for KDE Connect, ADB, Syncthing, Tailscale, audio routing, and system integration.

## Current Core Backends

- `backend/kdeconnect.py`
  D-Bus wrapper for paired-device state, notifications, telephony signals, clipboard, SMS, and KDE notification suppression policy.
- `backend/adb_bridge.py`
  ADB facade for target resolution, direct device commands, media queries, telephony fallback state, screenshots, recordings, and mirroring helpers.
- `backend/syncthing.py`
  Syncthing REST client plus local config fallback and runtime state semantics.
- `backend/tailscale.py`
  Tailscale CLI wrapper with mesh readiness semantics.
- `backend/audio_route.py` and `backend/call_audio.py`
  Call-route state machine and temporary device/volume application during laptop-routed calls.
- `backend/system_integration.py` and `backend/autostart.py`
  Desktop entry, icon, Hyprland keybind integration, and immutable runtime publishing for the systemd user service.

## Stability Design

PhoneBridge is built around conservative fallbacks:

- KDE Connect remains the primary event source for notifications and calls.
- ADB fills gaps where KDE Connect is missing or stale.
- Controllers reduce aggressive polling when signal health is good and speed up only when call state is active or degraded.
- Runtime publishing moves the autostarted app away from the mutable working tree and onto a validated release snapshot under `~/.local/share/phonebridge/runtime/current`.

## Known Technical Debt

These are still active refactor targets:

- `ui/window.py`, `ui/components/call_popup.py`, `backend/adb_bridge.py`, and `backend/kdeconnect.py` are still larger than they should be.
- Several UI pages still contain too much local styling and should converge on shared theme components.
- ADB and KDE Connect facades still need further internal extraction into smaller implementation modules.

## Public Release Rule

This repository is public-ready only when:

- the deterministic test suite passes,
- the runtime publish smoke check passes,
- the autostarted service runs from `runtime/current`,
- and no machine-specific paths or audit-only artifacts remain in tracked files.
