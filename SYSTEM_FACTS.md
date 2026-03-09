# SYSTEM_FACTS

## 2026-03-03 — Call routing references

1. Android telephony call-state constants are `CALL_STATE_IDLE` (0), `CALL_STATE_RINGING` (1), and `CALL_STATE_OFFHOOK` (2).
   - Source: https://developer.android.com/reference/android/telephony/TelephonyManager

2. scrcpy audio behavior relevant to call/media routing:
   - `--audio-source=output` forwards device output and disables playback on device.
   - `--audio-source=playback` captures playback on Android 13+ and can duplicate with `--audio-dup`.
   - `--audio-source=voice-call`/`voice-call-uplink`/`voice-call-downlink` are available sources.
   - Source: https://github.com/Genymobile/scrcpy/blob/master/doc/audio.md

3. KDE Connect telephony plugin event names include `ringing`, `talking`, `missedCall`, and `sms` in packet `event` field.
   - Source: https://invent.kde.org/network/kdeconnect-kde/-/blob/master/plugins/telephony/README

4. PhoneBridge currently binds KDE telephony D-Bus signal `callReceived` from interface `org.kde.kdeconnect.device.telephony`.
   - Local source: backend/kdeconnect.py

5. PhoneBridge fallback call-state polling maps ADB `dumpsys telephony.registry` `mCallState` values to `idle/ringing/offhook`.
   - Local source: backend/adb_bridge.py

## 2026-03-05 — Hyprland popup visibility (call popup investigation)

6. **Hyprland v0.54.0** installed (`0002f148c9a4fe421a9d33c0faa5528cdc411e62`, built 2026-02-27).
   - `windowrulev2` keyword is **deprecated** in v0.54; use `windowrule` with new syntax.
   - `windowrule` new format: `windowrule = <effect> <value>, match:<prop> <regex>`
     - Example: `windowrule = float on, match:title ^(PhoneBridge Call)$`
   - `/keyword windowrule 'float on, match:title ^...$'` via IPC returns "ok" ✓
   - `hyprctl dispatch setfloating title:^...$` — valid runtime dispatch ✓
   - `hyprctl dispatch pin title:^...$` — valid runtime dispatch ✓
   - `hyprctl dispatch alterzorder top, title:^...$` — valid runtime dispatch ✓
   - `hyprctl dispatch float` — **Invalid dispatcher** (removed in v0.54)

7. **Popup compositor state confirmed**: `hyprctl clients` shows the popup at `at: 1600,54`,
   `size: 300,x`, `floating: 1`, `pinned: 1`, `xwayland: 0` within 200 ms of signal injection.
   The popup IS correctly created as a native Wayland xdg_toplevel.  Must query within 200 ms
   because the ADB poller closes it at 650 ms in test scenarios (no real call).

8. **Root cause — Caelestia drawers layer obscures popup**: The system runs Caelestia shell which
   registers a `zwlr_layer_shell_v1` surface at **layer level 2 (top)** covering the full screen
   (0 0 1920 1080), namespace `caelestia-drawers`. In Hyprland's rendering order:
   ```
   level 3 overlay > level 2 top (caelestia-drawers) > FLOATING WINDOWS > tiled > level 1 bottom
   ```
   `alterzorder top` raises the popup within the floating window z-stack but CANNOT place it above
   layer-shell level 2 surfaces. This is a Wayland compositor constraint.
   - **Workaround applied**: `hyprctl notify` creates a notification at level 3 (overlay), always
     visible above Caelestia. Format: `hyprctl notify <icon_id> <time_ms> <rgb_color> <message>`
     - `hyprctl notify 0 20000 rgb(4f8ef7) "📞 Incoming call from Mom"` → "ok" ✓

9. **Hyprland IPC socket accessible inside steam-run bwrap**: `HYPRLAND_INSTANCE_SIGNATURE` and
   `XDG_RUNTIME_DIR` are propagated into the steam-run sandbox. Socket at
   `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock` is writable from inside bwrap.

10. **Qt6 platform inside steam-run**: `app.platformName()` returns `"wayland"` (native Wayland).
    Both main window and popup show `xwayland: 0` in `hyprctl clients`.

11. **KDE Connect live signal args** (confirmed 2026-03-05):
    - arg[0]=`"callReceived"` (ringing), arg[1]=`"+919886787942"`, arg[2]=`"Mom"`
    - arg[0]=`"missedCall"` arrives ~5 seconds after `"callReceived"` (not simultaneously)
    - `"callReceived"` normalizes to `"ringing"` via `normalize_call_event`.

12. **ADB poller race in test scenarios**: `_state_watch_timer` fires every 650 ms. In tests with no
    real ADB call, `get_call_state()` returns `"idle"` → popup closes at 650 ms. During real calls,
    returns `"ringing"` (popup stays). If ADB unavailable, returns `"unknown"` (poller skips safely).
