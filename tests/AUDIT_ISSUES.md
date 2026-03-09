# PhoneBridge Deterministic Audit Register

Date: 2026-03-10  
Scope: test-only audit additions; no production files changed.

## Baseline
- Command: `PYTHONPATH=. ./.venv/bin/pytest -q -m 'not hardware'`
- Result after test-only updates: `225 passed, 31 skipped`
- Remaining skips are environment-driven, not assertion failures.
- Skip drivers in this environment:
  - `libGL.so.1` missing for Qt widget imports in `tests/qt/*`
  - `libstdc++.so.6` missing for PyQt runtime imports in `tests/unit/test_runtime_smoke.py` and `tests/unit/test_window_runtime_refresh_policy.py`

## Confirmed Issues

| Severity | Type | Area | Proof | Why it is unsafe |
| --- | --- | --- | --- | --- |
| High | Race / correctness | `backend/state.py` live mutable reads | `test_app_state_get_exposes_live_mutable_reference_without_listener_notification` | `AppState.get()` exposes a live mutable object; callers can mutate shared state without `set()`/`update()` and listeners never see the change. |
| High | Race / consistency | `backend/state.py` multi-key state | `test_app_state_sequential_multi_key_updates_expose_torn_snapshot_to_listeners` | Sequential `set()` calls let listeners observe a mixed snapshot across related keys. |
| Medium | Re-entrancy | `backend/state.py` listener delivery | `test_app_state_reentrant_listener_updates_deliver_nested_notifications_immediately` | Listeners can synchronously trigger nested state changes and downstream notifications with no transaction boundary. |
| High | UI blocking | `ui/components/call_popup_session.py` | `test_call_popup_actions_run_sync_backend_calls_on_the_ui_path` | Popup answer/reject/end/mute/callback/reply-diversion actions call ADB/call-control code directly on the UI path. |
| High | Unsafe cancellation | `ui/components/call_popup_session.py` | `test_call_popup_close_event_uses_terminate_as_fallback_for_route_worker` | Popup route shutdown escalates to `terminate()`, which can kill a worker mid-side-effect. |
| High | Unsafe cancellation | `ui/pages/calls.py` | `test_calls_page_route_worker_cancel_path_terminates_running_thread` | Calls page route cancellation also uses `terminate()` and does not wait for safe teardown. |
| High | Stale async completion | `ui/pages/calls.py` | `test_calls_page_route_result_handler_reads_shared_context_without_worker_identity` | Route completion reads shared `self._call_route_context` instead of per-worker identity, so late completions can write stale UI state. |
| High | Lost work | `ui/window_runtime.py` | `test_sync_audio_route_async_drops_follow_up_request_while_busy` | A second audio-route sync request is dropped outright while one is in flight; it is not queued or coalesced. |
| High | Lost work | `ui/window_runtime.py` | `test_sync_notification_snapshot_drops_second_refresh_while_busy` | Notification snapshot refresh requests are dropped while busy, which can miss a later state transition. |
| Medium | Lost work | `ui/window_runtime.py` | `test_poll_phone_call_state_async_drops_second_poll_while_busy` | Telephony poll requests are dropped while a prior poll is in flight. |
| Medium | Contention | `backend/connectivity_controller.py` | `test_connectivity_same_operation_lock_rejects_reentry`, `test_connectivity_cross_operation_locks_allow_overlap` | Same-op contention is blocked, but cross-op overlap is allowed, so Bluetooth/Wi-Fi/KDE/Syncthing actions can interleave against the same phone/runtime state. |
| Medium | UI blocking | `ui/runtime_controllers.py` | `test_clipboard_controller_source_uses_blocking_subprocess_polling` | Wayland clipboard fallback uses synchronous `subprocess.run()` in the controller path. |
| Medium | Lifecycle / shutdown | `ui/window_support.py` | `test_dbus_signal_bridge_start_and_stop_do_not_track_or_join_worker_thread` | The GLib/D-Bus background thread is not stored or joined during stop, so shutdown/restart semantics are not fully controlled. |

## New Audit Tests
- `tests/unit/test_audit_state_and_call_risks.py`
- `tests/unit/test_audit_runtime_and_connectivity_risks.py`

Both files were rerun 5 consecutive times with:

```bash
for i in 1 2 3 4 5; do
  PYTHONPATH=. ./.venv/bin/pytest -q \
    tests/unit/test_audit_state_and_call_risks.py \
    tests/unit/test_audit_runtime_and_connectivity_risks.py
done
```

All 5 runs passed (`17 passed` each run).

## Mounted Flow Matrix

| Surface | Deterministic evidence now | Explicit blockers / gaps |
| --- | --- | --- |
| Dashboard | Existing source/runtime logic coverage around connectivity state and audio redirect; pre-existing Qt shape test exists | Quick actions, media switcher, clipboard dialog, DND worker lifecycle, and button-to-backend wiring still need Qt-runtime execution; blocked here by missing PyQt runtime libs |
| Messages | Existing deterministic notification mirror/open/dismiss tests in `tests/unit/test_notification_dedup_ordering.py` | Quick reply, SMS compose/send UI, contact picker, and visible refresh behavior still need Qt-runtime execution |
| Calls page | Existing deterministic outbound/call-route reducer coverage plus new audit tests for route cancellation and stale completion | Dialpad/contact/history rendering and user interaction still need Qt-runtime execution |
| Call popup | New audit tests prove sync blocking and unsafe cancellation; existing popup Qt tests cover route-summary behavior when Qt runtime is available | Close gating, reply diversion, live popup transitions, and duplicate-event UI behavior still need Qt-runtime execution |
| Files | Existing deterministic helpers plus file-flow harness cover add/open/mkdir/remove custom-folder paths | Send/open/path-edit/sync/delete/load-more visual flows still need Qt-runtime execution |
| Mirror | No strong deterministic UI execution in current environment | Mode switch, launch/stop, screenshot/record/webcam controls, and live indicator all remain blocked by lack of Qt runtime and real process integration |
| Network | New audit tests prove lock semantics and overlap risk | Actual toggle UI flows and refresh rendering still need Qt-runtime execution |
| Settings | Existing deterministic call-audio and autostart/runtime tests remain valid | Open Syncthing UI, Force Kill, and full widget interaction still need Qt-runtime execution |
| Shell / runtime | Existing deterministic notification-open diversion, reducer logic, suppression policy, and new busy-flag audit tests cover a large portion of runtime behavior | Window show/hide/toggle, Hyprland interaction, and popup-placement flows still rely on hardware/runtime harnesses |

## Environment Blockers
- Qt widget suites are present but skipped in this environment due missing host libraries.
- Real telephony remains unavailable; call behavior is simulated through reducers, source audit, and state/worker tests only.
- Hardware harnesses remain supplemental, not primary evidence.
