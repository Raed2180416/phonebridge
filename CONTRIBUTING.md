# Contributing

PhoneBridge is currently maintained for a narrow runtime target:

- NixOS
- Hyprland
- KDE Connect
- ADB-enabled Android device

Contributions that improve stability, maintainability, diagnostics, and tests for that stack are the best fit.

## Development Commands

Run the app:

```bash
./run-venv-nix.sh
```

Run the full deterministic suite:

```bash
./.venv/bin/pytest -q -rs
```

Run the prepublish gate:

```bash
./scripts/prepublish_check.sh
```

If you want the installed app to track repo edits automatically during development while still running from `runtime/current`, run:

```bash
./scripts/dev_runtime_watch.sh
```

## Test Markers

- `hardware`
  Requires a real machine or phone setup and is not expected to run in CI.
- `qt_runtime`
  Exercises Qt/controller behavior and may skip when host runtime libraries are missing.

## Contribution Rules

- Keep the current supported scope explicit. Do not silently broaden platform claims in docs.
- Prefer targeted refactors over rewrites.
- New runtime behavior should have deterministic tests unless it is inherently hardware-only.
- If a change affects startup, notifications, calls, or autostart publishing, verify the runtime path and service behavior locally.
