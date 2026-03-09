# PhoneBridge Deterministic Remediation Register

Date: 2026-03-10
Snapshot baseline: branch `snapshot/2026-03-10-pre-deterministic-remediation`
Snapshot commit/tag: `70d2d80` / `snapshot-2026-03-10-pre-deterministic-remediation`

## Current Deterministic Baseline
- Command: `./scripts/run_pytest_nix.sh -q -m 'not hardware'`
- Current result on the remediation worktree: `263 passed`
- Repeated stability rerun:

```bash
for i in 1 2 3 4 5; do
  ./scripts/run_pytest_nix.sh -q \
    tests/unit/test_audit_state_and_call_risks.py \
    tests/unit/test_audit_runtime_and_connectivity_risks.py \
    tests/unit/test_window_runtime_refresh_policy.py \
    tests/unit/test_dbus_signal_bridge_lifecycle.py \
    tests/qt/test_call_surface_cleanup.py \
    tests/qt/test_call_route_defaults.py \
    tests/qt/test_runtime_controllers.py
done
```

- Result: all 5 reruns passed (`48 passed` each run).

## Fixed Issue Register

| Previous Risk | Status | Proof | Remediation Summary |
| --- | --- | --- | --- |
| `AppState.get()` leaked live mutable state | Fixed | `test_app_state_get_returns_isolated_mutable_copy_and_preserves_listener_contract` | `backend/state.py` now clones mutable state on read/write, so callers cannot mutate global state behind listeners. |
| Related keys could be written with torn snapshots | Fixed | `test_app_state_set_many_updates_related_keys_atomically_for_listeners` | `backend/state.py` now exposes `set_many()` and correlated call/runtime writes were migrated to it. |
| Re-entrant listeners delivered nested notifications mid-transaction | Fixed | `test_app_state_reentrant_listener_updates_are_deferred_until_outer_listener_returns` | AppState now drains queued notifications after the outer dispatch completes. |
| Popup answer/reject/end/mute/callback/SMS diversion blocked the UI path | Fixed | `test_call_popup_actions_dispatch_backend_work_async_and_do_not_call_adb_inline` | `ui/components/call_popup_session.py` now routes popup actions through an async action runner with token-bound completion handling. |
| Popup route worker shutdown used `terminate()` | Fixed | `test_call_popup_close_event_uses_cooperative_worker_shutdown_only` | Popup close now requests interruption, clears route intent, waits briefly, and detaches without force-killing the worker. |
| Calls page route cancellation used `terminate()` | Fixed | `test_calls_page_route_worker_cancel_path_uses_request_id_and_cooperative_shutdown` | Calls page route workers now use cooperative interruption and stale-result invalidation instead of thread termination. |
| Calls page route completion could write stale shared context | Fixed | `test_calls_page_route_result_handler_is_bound_to_request_id_and_worker_context` | Route completion is now bound to request id + worker-local context, and late results are ignored. |
| Audio-route sync dropped follow-up work while busy | Fixed | `test_sync_audio_route_async_coalesces_follow_up_request_and_merges_suspend_flags` | `ui/window_runtime.py` now coalesces one trailing rerun and merges suspend intent instead of dropping requests. |
| Notification snapshot refresh dropped follow-up work while busy | Fixed | `test_sync_notification_snapshot_coalesces_second_refresh` | Runtime snapshot refresh now performs one trailing rerun when a request arrives mid-flight. |
| Telephony poll dropped follow-up work while busy | Fixed | `test_poll_phone_call_state_async_coalesces_trailing_poll` | Call polling now remembers one trailing rerun and reschedules after the in-flight poll is applied. |
| Connectivity controller allowed cross-operation overlap | Fixed | `test_connectivity_same_operation_lock_rejects_reentry`, `test_connectivity_cross_operation_locks_are_serialized` | `backend/connectivity_controller.py` now serializes consequential ops through a single active-operation gate. |
| Clipboard controller used synchronous Wayland subprocess polling on the controller path | Fixed | `test_clipboard_controller_reads_cached_wayland_text_and_moves_subprocess_work_to_helper`, `test_clipboard_controller_records_remote_and_local_text` | `ui/runtime_controllers.py` now reads cached Wayland text immediately and moves `wl-paste` work to a background helper thread. |
| D-Bus bridge did not own/join its background thread on stop | Fixed | `test_dbus_signal_bridge_tracks_and_joins_worker_thread`, `test_signal_bridge_reconnect_disconnects_old_receivers` | `ui/window_support.py` now stores the GLib thread handle, makes `start()`/`stop()` idempotent, and joins on stop. |

## Supporting Regression Coverage
- Call/runtime flow stability:
  - `test_polled_call_plan_synthesizes_ringing_for_stale_ui`
  - `test_calls_page_incoming_resets_pc_route_request`
  - `test_calls_page_outbound_active_auto_routes_on_talking`
  - `test_polled_ringing_edge_opens_popup_immediately`
  - `test_polled_idle_finalizes_live_session_even_if_public_call_ui_row_is_stale`
- Popup and Calls page UX surfaces:
  - `test_call_popup_route_summary_and_mute_visibility_follow_call_route_ui_state`
  - `test_call_popup_warmup_stays_hidden_until_activation`
  - `test_calls_page_places_call_without_sync_adb_block`
  - `test_calls_page_background_workers_do_not_bind_stale_adb_target`
  - `test_calls_page_terminal_cleanup_clears_mute_async`
- Runtime controller coverage:
  - `test_call_controller_adapts_interval_by_state_and_visibility`
  - `test_health_and_connectivity_controllers_manage_timers`
  - `test_notification_controller_schedules_startup_callbacks`

## Residual Limits
- Real telephony and live hardware/audio stacks are still simulated. The deterministic suite now proves the software-side race, blocking, stale-completion, and lifecycle fixes, but it does not replace hardware acceptance on actual KDE Connect + Android + Bluetooth hardware.
- Hyprland compositor behavior, real D-Bus daemon churn, and live BT profile edge cases still depend on external runtime/hardware conditions. Those remain acceptance-test concerns, not deterministic unit guarantees.
- The suite is now configured to run under `./scripts/run_pytest_nix.sh` without manually re-adding Qt runtime libraries for this project.
