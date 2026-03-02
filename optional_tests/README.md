# Optional Test + Hardware Harness Pack

This directory is intentionally self-contained so it is easy to remove.

Contents:
- `test_audio_route_state_machine.py`: deterministic pytest coverage for call/audio route transitions (including call-mic profile gating).
- `test_outbound_popup_suppression.py`: deterministic pytest coverage for outbound-call popup suppression + cleanup behavior.
- `test_call_mic_activation_transition.py`: deterministic pytest coverage for pending -> active call-mic transition retry logic.
- `test_kde_watchdog.py`: deterministic pytest coverage for KDE watchdog debounce/cooldown + Tailscale/ADB gate checks.
- `test_kde_phone_commands.py`: deterministic pytest coverage for KDE command-pack installer + phone-triggered host action handlers.
- `test_tailscale_mesh_status.py`: deterministic pytest coverage for mesh snapshot semantics (local online, phone peer presence, mesh-ready decision).
- `hardware_call_mic_harness.py`: scripted environment + route verification harness for real hardware checks.

Run deterministic tests:

```bash
PYTHONPATH=. pytest -q optional_tests/test_audio_route_state_machine.py optional_tests/test_outbound_popup_suppression.py
```

Run hardware harness (inspect only):

```bash
PYTHONPATH=. python3 optional_tests/hardware_call_mic_harness.py --no-route-mutation
```

Run hardware harness (active route verification):

```bash
PYTHONPATH=. python3 optional_tests/hardware_call_mic_harness.py
```

Removal:
- Delete the `optional_tests/` directory. No app/runtime code depends on it.
