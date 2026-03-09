# PhoneBridge

PhoneBridge is a Qt6 desktop shell for controlling a phone from Linux by composing tools that already exist:

- KDE Connect for notifications, SMS, clipboard, telephony metadata, and file send
- ADB and scrcpy for direct Android control and recovery paths
- Syncthing for folder sync
- Tailscale for mesh reachability

The project is public, but the supported scope for v1 is intentionally narrow:

- Host OS: `NixOS`
- Compositor: `Hyprland`
- Phone bridge: `KDE Connect + ADB`
- Launch model: source-run app plus systemd user service

Anything outside that matrix should be treated as experimental.

<p align="center">
  <img src="pics/maindash.png" alt="PhoneBridge main dashboard" width="900" />
</p>

## What It Does

PhoneBridge is meant to make the existing phone-on-desktop stack behave like one coherent app instead of four separate tools.

- Notifications
  Mirrors phone notifications into the shell UI with reply, copy, open, and bidirectional dismiss behavior.
- Calls
  Shows incoming call popups, lets you dial from the desktop, and can route active calls to the laptop audio stack.
- Messages
  Loads threads and contacts, then sends SMS from the desktop UI.
- Files
  Surfaces Syncthing folder state, path overrides, file browsing, and KDE Connect send flows in one place.
- Connectivity
  Exposes Tailscale, Syncthing, Wi-Fi, Bluetooth, and related recovery state through shared status snapshots.
- Mirroring and device controls
  Uses ADB and scrcpy for screen mirroring, screenshots, recording, radios, and other direct device actions.

## Screens

<p align="center">
  <img src="pics/network.png" alt="Connectivity controls" width="900" />
</p>
<p align="center">
  <img src="pics/calls.png" alt="Calls interface" width="900" />
</p>
<p align="center">
  <img src="pics/callpopup.png" alt="Incoming call popup" width="520" />
</p>
<p align="center">
  <img src="pics/notifpanel.png" alt="Notification panel" width="900" />
</p>
<p align="center">
  <img src="pics/filebrowser.png" alt="File browser" width="900" />
</p>
<p align="center">
  <img src="pics/screenmirror.png" alt="Screen mirroring view" width="900" />
</p>
<p align="center">
  <img src="pics/webcam.png" alt="Webcam mode" width="900" />
</p>
<p align="center">
  <img src="pics/settings.png" alt="Settings page" width="900" />
</p>

## Quick Start

### Dependencies

Required:

- `python3`
- `PyQt6`
- `adb`
- `kdeconnectd`
- `tailscale`
- `syncthing`
- `steam-run`

Feature-specific:

- `scrcpy` for mirroring
- `wpctl` or `pactl` for call audio routing
- `ffmpegthumbnailer` or `ffmpeg` for video thumbnails
- `wl-copy` or `xclip` for clipboard copy actions

### Run From Source

On the target setup, the intended entrypoint is:

```bash
./run-venv-nix.sh
```

This wrapper bootstraps the runtime through `steam-run` when Qt or D-Bus dependencies are missing from the local venv context.

### Autostart

PhoneBridge can install a systemd user service from Settings. The service should run from the published runtime snapshot, not the live checkout:

```text
~/.local/share/phonebridge/runtime/current
```

For development, you can keep the installed app on `runtime/current` and auto-republish it after repo edits:

```bash
./scripts/dev_runtime_watch.sh
```

Or enable the dev-only watcher service:

```bash
./scripts/dev_runtime_watch_service.sh enable
```

This keeps the installed app snapshot-based. The watcher republishes `runtime/current` after edits; it does not point the installed app directly at the repo checkout.

### Optional Helpers

Install the KDE watchdog:

```bash
./scripts/install_kde_watchdog.sh \
  --device-id <kde-device-id> \
  --phone-ip <phone-tailscale-ip> \
  --adb-target <adb-target-ip:port> \
  --enable
```

Install KDE Connect phone commands:

```bash
./scripts/install_kde_phone_commands.sh --device-id <kde-device-id>
```

## Runtime Configuration

Runtime configuration lives in:

```text
~/.config/phonebridge/settings.json
```

Documented environment overrides:

- `PHONEBRIDGE_ADB_TARGET`
- `PHONEBRIDGE_DEVICE_ID`
- `PHONEBRIDGE_DEVICE_NAME`
- `PHONEBRIDGE_PHONE_TAILSCALE_IP`
- `PHONEBRIDGE_HOST_TAILSCALE_IP`
- `PHONEBRIDGE_SYNCTHING_URL`
- `PHONEBRIDGE_SYNCTHING_API_KEY`

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the full contract.

`PHONEBRIDGE_ADB_TARGET` is optional. PhoneBridge treats it as an ADB hint, not as a permanent hard binding to one serial string.

## Stability Notes

PhoneBridge is designed to fail conservatively:

- missing optional tools should degrade the relevant feature instead of crashing the app
- notification and call behavior prefer KDE Connect signals and fall back to ADB where needed
- autostart publishes an immutable runtime snapshot before enabling the service
- desktop integration writes are opt-in

There is still active refactoring work underway, especially in large runtime modules such as the shell window, call popup, ADB facade, and KDE Connect facade.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/PUBLISHING.md](docs/PUBLISHING.md)
- [docs/PHASE2_KDE_NOTIFICATION_ACTION_PATCH.md](docs/PHASE2_KDE_NOTIFICATION_ACTION_PATCH.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)

## Tests

Run the public deterministic suite:

```bash
./.venv/bin/pytest -q -rs
```

Run the local prepublish gate:

```bash
./scripts/prepublish_check.sh
```

Test tiers are documented in [tests/README.md](tests/README.md).

## License

PhoneBridge is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
