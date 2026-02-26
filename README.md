# PhoneBridge

PhoneBridge is a desktop Qt6 control plane that centralizes phone interactions (KDE Connect, ADB/scrcpy, Syncthing, Tailscale) into a single GUI.

See `/.github/copilot-instructions.md` for developer notes and priority tasks.

Quick start

- Install dependencies for the Qt6 app (e.g., PyQt6 or PySide6).
- Run the app:

```
python3 main.py
```

Project layout

- `main.py` — entrypoint and app lifecycle
- `backend/` — systems layer (ADB, KDE Connect, Syncthing, Tailscale, settings)
- `ui/` — Qt UI, theme, components and page implementations
