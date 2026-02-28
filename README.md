# PhoneBridge

PhoneBridge is a desktop Qt6 control plane that centralizes phone interactions (KDE Connect, ADB/scrcpy, Syncthing, Tailscale) into a single GUI.

See `/.github/copilot-instructions.md` for developer notes and priority tasks.
For a full technical walkthrough, see `docs/PHONEBRIDGE_DEEP_DIVE.md`.

Quick start

- Install dependencies for the Qt6 app (e.g., PyQt6 or PySide6).
- Run the app:

```
python3 main.py
```

On NixOS, `main.py` now self-heals for common runtime issues (`libGL.so.1`,
missing `dbus` in venv) by re-executing itself through `steam-run`.

NixOS + local venv

- If you run with `./.venv/bin/python`, use:

```
./run-venv-nix.sh
```

- This wrapper runs through `steam-run` (for foreign wheel runtime libs like `libGL.so.1`)
  and exports system `dbus-python` to the venv process.
- Wrapper remains the canonical launch path for systemd autostart service.

Start on login (systemd user service)

- Toggle from Settings → System → Start on Login.
- Service unit: `~/.config/systemd/user/phonebridge.service`
- `ExecStart` uses `run-venv-nix.sh --background`.

Project layout

- `main.py` — entrypoint and app lifecycle
- `backend/` — systems layer (ADB, KDE Connect, Syncthing, Tailscale, settings)
- `ui/` — Qt UI, theme, components and page implementations
