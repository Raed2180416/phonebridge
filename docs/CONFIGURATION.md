# Configuration

PhoneBridge is configured through a single settings file plus a small set of environment overrides.

## Settings File

Persistent settings live at:

```text
~/.config/phonebridge/settings.json
```

The settings store is schema-lite. Unknown keys are ignored, obsolete keys are dropped on load, and valid user settings are preserved.

## Supported Environment Overrides

These variables override the corresponding persisted settings:

| Environment variable | Setting key | Purpose |
| --- | --- | --- |
| `PHONEBRIDGE_ADB_TARGET` | `adb_target` | Optional ADB serial or host:port hint |
| `PHONEBRIDGE_DEVICE_ID` | `device_id` | KDE Connect device ID |
| `PHONEBRIDGE_DEVICE_NAME` | `device_name` | Human-readable phone name |
| `PHONEBRIDGE_PHONE_TAILSCALE_IP` | `phone_tailscale_ip` | Phone Tailscale IP |
| `PHONEBRIDGE_HOST_TAILSCALE_IP` | `nixos_tailscale_ip` | Host Tailscale IP |
| `PHONEBRIDGE_SYNCTHING_URL` | `syncthing_url` | Syncthing REST base URL |
| `PHONEBRIDGE_SYNCTHING_API_KEY` | `syncthing_api_key` | Syncthing API key |

These are the only documented public environment variables for v1.

`PHONEBRIDGE_ADB_TARGET` is treated as a hint, not a hard binding. PhoneBridge will still prefer the currently connected intended phone when it can resolve that from USB connectivity, the configured phone Tailscale IP, or the device-name hint.

## Runtime Defaults

Important defaults:

- `sync_root`: `~/PhoneSync`
- `phonesend_dir`: `~/PhoneSync/PhoneSend`
- `syncthing_url`: `http://127.0.0.1:8384`
- `device_name`: `Phone`
- `theme_name`: `slate`
- `motion_level`: `subtle`
- integration writes are opt-in by default

## Integration Writes

PhoneBridge does not mutate desktop integration automatically on first run.

These actions are opt-in from Settings:

- install the app icon
- install the desktop entry
- manage the Hyprland `SUPER+P` toggle bind
- enable the autostart systemd user service

## Runtime Publishing

The autostarted service should run from:

```text
~/.local/share/phonebridge/runtime/current
```

That location is a published release snapshot, not the live repository checkout.

For development, repo edits do not affect the installed app until the runtime is republished. To automate that without changing the installed app contract, run:

```bash
./scripts/dev_runtime_watch.sh
```

Or enable the dev-only watcher service:

```bash
./scripts/dev_runtime_watch_service.sh enable
```

## Manual Setup Checklist

Minimum host dependencies:

- Python 3
- PyQt6
- `adb`
- `kdeconnectd`
- `tailscale`
- `syncthing`
- `steam-run`

Feature-specific tools:

- `scrcpy` for mirroring
- `wpctl` or `pactl` for audio routing
- `ffmpegthumbnailer` or `ffmpeg` for video thumbnails
- `wl-copy` or `xclip` for clipboard copy actions

## Out Of Scope For Public v1

These are intentionally not release blockers:

- broad non-NixOS support
- non-Hyprland compositor support
- packaging as PyPI, AppImage, or Flatpak
- full KDE Connect action parity without the optional KDE patch in `docs/PHASE2_KDE_NOTIFICATION_ACTION_PATCH.md`
