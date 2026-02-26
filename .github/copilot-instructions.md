# PHONEBRIDGE — AGENT INSTRUCTION SHEET

Purpose
-------
PhoneBridge is a desktop Qt6 control plane that centralizes all phone interactions (KDE Connect,
ADB/scrcpy, Syncthing, Tailscale) into a single control surface. The goal is to route ALL phone
interaction through this GUI instead of relying on KDE Connect’s default UI.

Project layout (high level)
---------------------------
phonebridge/
- `main.py` — entrypoint and app lifecycle
- `backend/` — systems layer (ADB, KDE Connect, Syncthing, Tailscale, settings)
- `ui/` — Qt UI, theme, components and page implementations

File-by-file breakdown (concise)
--------------------------------
- `main.py`: Launches Qt app, integrates GLib D-Bus mainloop, creates IPC socket at `/tmp/phonebridge-<uid>.sock`, triggers startup checks. Must NOT hold business logic.
- `backend/adb_bridge.py`: ADB helpers + `launch_scrcpy()` (note: sets `WAYLAND_DISPLAY` and `--render-driver opengl`), screenshot, send text, hotspot/settings opener, dnd, battery reads.
- `backend/kdeconnect.py`: D-Bus wrapper for KDE Connect plugins (battery, notifications, sms, share, clipboard, telephony signals, sftp, contacts). Exposes listener registration methods like `connect_call_signal()`.
- `backend/settings_store.py`: Persistent JSON settings at `~/.config/phonebridge/settings.json`.
- `backend/startup_check.py`: Tailscale/KDE Connect/Syncthing checks; contains an embedded Syncthing `API_KEY`.
- `ui/window.py`: Page registry (`PAGES`), `DBusSignalBridge` (bridges GLib D-Bus to Qt signals), `PhoneBridgeWindow` (page switching, polling, global handlers).

Current critical failures (discovered from codebase and runtime notes)
-----------------------------------------------------------------
- Clipboard: No receiver for phone→desktop clipboard. `kdeconnect.sendClipboard()` only pushes local→phone.
- Notifications: Signal receiver likely not attached or UI not refreshing; notifications fetched but not rendered.
- SMS: `send_sms()` may use wrong D-Bus types; conversations not loaded; missing listeners for conversation updates.
- Contacts: `get_cached_contacts()` reads vCard paths; path or permission mismatch likely.
- Calls: Can place calls via ADB but cannot answer calls via telephony D-Bus; call popup crashes (likely Qt thread misuse).
- Hotspot toggle: Currently opens settings only. Use `adb shell cmd wifi start-softap` / `stop-softap` or `svc wifi enable/disable` depending on Android version.
- Webcam mode: scrcpy `--no-playback` prevents display; v4l2 sink needs a viewer (ffplay/VLC) attached to `/dev/video2`.
- File browser crashes on back navigation (likely widget/layout disposal bug).

Required architectural changes
----------------------------
1. Central State Manager: add `backend/state.py` to maintain canonical state (notifications, contacts, sms_threads, call_state, clipboard, connection_status). UI subscribes to state changes instead of directly polling backends.
2. Remove redundant/unused UI controls (Find Device, duplicate audio buttons, unused mirror controls).
3. Implement missing features: Bluetooth toggle (`adb shell svc bluetooth enable/disable`), hotspot start/stop, folder add UI for Syncthing, contact chooser in SMS, telephony answer/transfer.

Priority tasks (do these first)
------------------------------
1. Fix crash stability (call popup, file back navigation)
2. Fix notifications rendering and signal wiring
3. Fix SMS sending and conversation sync
4. Fix clipboard receive flow
5. Implement call answering via telephony D-Bus
6. Implement hotspot toggle commands
7. Remove useless UI elements; redesign theme (`ui/theme.py`) and toggles to match mockup

Implementation guidance (practical rules)
---------------------------------------
- Always inspect runtime logs (`phonebridge.log`) before changing code. Follow `AGENT_PROTOCOL.MD` deterministic debug flow.
- Add structured logging to new/modified backend code. Create `backend/logger.py` as central initializer per `AGENT_PROTOCOL.MD` and import `log = logging.getLogger(__name__)` in modules.
- When modifying D-Bus or ADB commands: capture command output with subprocess, test on multiple Android versions if possible, and record any device-specific facts to `SYSTEM_FACTS.md`.
- Keep UI thread safety rules: only call Qt widgets from the Qt main thread. Use `QTimer.singleShot()` or `pyqtSignal` to marshal background results to the UI thread.
- When adding new features that involve async signals (D-Bus callbacks), use `DBusSignalBridge` pattern and emit Qt signals consumed by UI components.

Local examples & snippets
-------------------------
- Page registration: see `ui/window.py` `PAGES` list; to add a page add tuple `(icon, title, PageClass, page_id)` and implement `PageClass` under `ui/pages/`.
- Startup check invocation: `StartupChecker(window).run_and_show()` (used from `main.py` and tray menu).
- Scrcpy launch must preserve `--render-driver opengl` and set `WAYLAND_DISPLAY` in env (see `backend/adb_bridge.launch_scrcpy`).

Research requirement
--------------------
For ambiguous platform actions (hotspot commands, telephony D-Bus method names, scrcpy flags, clipboard behavior) search official docs and recent community examples. Confirm version compatibility and update `SYSTEM_FACTS.md` with tested commands and sources.

Agent behavior rules
--------------------
- Follow these instructions strictly for all work on this repo.
- Do not guess or make speculative fixes without logs or confirmatory queries (see `AGENT_PROTOCOL.MD`).
- Update `SYSTEM_FACTS.md` for each system fact discovered.

Next steps for me
-----------------
- I can scaffold `backend/state.py`, a `backend/logger.py` template, or implement any of the priority fixes—tell me which to start with.

