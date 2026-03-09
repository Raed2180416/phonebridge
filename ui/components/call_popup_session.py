"""Session and route-state behavior for the call popup."""

from __future__ import annotations

import logging
import threading
import time

from PyQt6.QtCore import Qt, QTimer

from backend import audio_route
from backend import call_controls
from backend.adb_bridge import ADBBridge
import backend.settings_store as settings
from backend.state import state
from ui.components.call_popup_route import BTRouteWorker

log = logging.getLogger(__name__)


class CallPopupSessionMixin:
    def _start_state_watcher(self):
        # The window runtime owns call-state polling and terminal resolution.
        # Running a second popup-local ADB poller creates duplicate telephony
        # traffic and can destabilize state over flaky wireless ADB links.
        self._stop_state_watcher()

    def _stop_state_watcher(self):
        if self._state_watch_timer.isActive():
            self._state_watch_timer.stop()

    def _poll_call_state(self):
        if self.current_state not in {"ringing", "talking"}:
            self._stop_state_watcher()
            return
        if getattr(self, "_state_poll_busy", False):
            return
        self._state_poll_busy = True

        def _run():
            try:
                result = ADBBridge().get_call_state_fast()
            except Exception:
                result = "unknown"
            self._poll_state_ready.emit(result or "unknown")

        threading.Thread(target=_run, daemon=True, name="pb-popup-state-poll").start()

    def _apply_polled_call_state(self, call_state: str):
        self._state_poll_busy = False
        if call_state == "unknown":
            return
        if self.current_state == "ringing":
            if call_state == "offhook":
                self._enter_talking(reset_timer=False)
                return
            if call_state == "idle":
                return
        if self.current_state == "talking" and call_state == "idle":
            return

    def _tick(self):
        self._call_seconds += 1
        m = self._call_seconds // 60
        s = self._call_seconds % 60
        self.timer_label.setText(f"{m:02d}:{s:02d}")

    @staticmethod
    def _normalize_status(raw: str) -> str:
        key = (raw or "").strip().lower().replace("-", "_")
        mapping = {
            "ringing": "ringing",
            "incoming": "ringing",
            "callreceived": "ringing",
            "talking": "talking",
            "accepted": "talking",
            "active": "talking",
            "missed_call": "missed_call",
            "missedcall": "missed_call",
            "missed": "missed_call",
            "ended": "ended",
            "disconnected": "ended",
            "idle": "ended",
            "declined": "ended",
            "rejected": "ended",
        }
        return mapping.get(key, "ended")

    def handle_call_event(self, number: str, contact_name: str, status: str):
        normalized = self._normalize_status(status)

        self.current_number = number or ""
        self.current_contact = contact_name or self.current_number
        self._refresh_contact()

        if normalized == "ringing":
            self._begin_call_session()
            self._enter_ringing()
        elif normalized == "talking":
            if self.current_state not in {"ringing", "talking"}:
                self._begin_call_session()
            self._enter_talking(reset_timer=False)
        elif normalized == "missed_call":
            self._enter_missed()
        else:
            self._enter_ended()

    def update_call_context(self, event: str, number: str, contact_name: str):
        normalized = self._normalize_status(event)
        if normalized == self.current_state:
            self.current_number = number or ""
            self.current_contact = contact_name or self.current_number
            self._refresh_contact()
            return
        self.handle_call_event(number, contact_name, event)

    def _enter_ringing(self):
        self.current_state = "ringing"
        state.set("call_muted", False)
        self._ringing_started_at = time.time()
        origin = str(state.get("call_origin", "unknown") or "unknown")
        self._is_outbound_call = origin in {"calls_page_outbound", "popup_answer_laptop"} or bool(getattr(self, "_is_outbound_call", False))

        self._show_popup()

        self._stop_label_pulse()
        self._set_top_bar("ringing")
        self._start_ringing_pulse()
        self._teardown_route(suspend_ui_global=True)

        self.timer_label.hide()
        self._call_timer.stop()
        self._call_seconds = 0
        self.timer_label.setText("00:00")

        self._set_close_gate(False)
        self._set_route_panel_visible(True)
        if getattr(self, "_is_outbound_call", False):
            self._set_primary_actions(
                "End Call",
                self._button_style("reject"),
                self.end_call,
                "Mute",
                self._button_style("mute", active=self._is_muted),
                self.toggle_mute,
                right_visible=False,
            )
        else:
            self._set_primary_actions(
                "Answer",
                self._button_style("answer"),
                self.answer_call,
                "Reject",
                self._button_style("reject"),
                self.reject_call,
            )
        self._set_extra_buttons(show_reply=False)

        if origin in {"calls_page_outbound", "popup_answer_laptop"}:
            self._set_laptop_pending()
            state.set("call_route_status", "pending_pc")
            state.set("call_route_reason", "Preparing laptop call audio...")
            state.set("call_route_backend", "none")
            self._publish_state("ringing", "pending_pc")
        else:
            self._set_phone_selected(reset_failure=True)
            self._publish_state("ringing", "phone")
        self._start_state_watcher()
        self._sync_popup_size()

    def _apply_default_route_for_origin(self):
        origin = str(state.get("call_origin", "unknown") or "unknown")
        route_status = str(state.get("call_route_status", "phone") or "phone")
        route_active = bool(state.get("call_audio_active", False)) or route_status == "pc_active"
        if origin == "calls_page_outbound":
            if route_active:
                self._sync_route_tiles_from_state()
                return
            self._set_laptop_pending()
            if route_status != "pending_pc":
                state.set("call_route_status", "pending_pc")
                state.set("call_route_reason", "Preparing laptop call audio...")
                state.set("call_route_backend", "none")
            self._publish_state("talking", "pending_pc")
            if self.current_state == "talking" and not self._route_busy:
                QTimer.singleShot(120, self.set_route_laptop)
            return
        if origin == "popup_answer_laptop":
            if route_active:
                self._sync_route_tiles_from_state()
                return
            self._set_laptop_pending()
            if route_status != "pending_pc":
                state.set("call_route_status", "pending_pc")
                state.set("call_route_reason", "Preparing laptop call audio...")
                state.set("call_route_backend", "none")
            if self.current_state == "talking" and not self._route_busy:
                QTimer.singleShot(220, self.set_route_laptop)
            return
        self.set_route_phone()

    def _enter_talking(self, *, reset_timer: bool):
        self.current_state = "talking"
        self._stop_ringing_pulse()
        self._set_top_bar("talking")
        self._start_label_pulse()

        if reset_timer:
            self._call_seconds = 0
            self.timer_label.setText("00:00")
        self.timer_label.show()
        if not self._call_timer.isActive():
            self._call_timer.start()

        self._set_close_gate(False)
        self._set_route_panel_visible(True)
        self._sync_talking_actions()
        self._set_extra_buttons(show_reply=False)

        self._show_popup()
        self._start_state_watcher()
        self._publish_state("talking")
        if not self._auto_route_applied:
            self._auto_route_applied = True
            self._apply_default_route_for_origin()
        self._sync_route_tiles_from_state()
        self._sync_popup_size()

    def _enter_missed(self):
        self.current_state = "missed_call"
        self._stop_state_watcher()
        self._invalidate_route_callbacks()
        self._teardown_route()
        self._stop_ringing_pulse()
        self._stop_label_pulse()
        self._set_top_bar("missed_call")

        self.timer_label.hide()
        self._call_timer.stop()

        self._set_close_gate(True)
        self._set_route_panel_visible(False)
        self._set_primary_actions(
            "Call Back",
            self._button_style("answer"),
            self.call_back,
            "Dismiss",
            self._button_style("neutral"),
            self.dismiss_missed,
        )
        self._set_extra_buttons(show_reply=False)

        self._publish_state("missed_call", "phone")
        self._show_popup()
        state.set("call_origin", "unknown")
        state.set("call_muted", False)
        self._sync_popup_size()

    def _enter_ended(self):
        self.current_state = "ended"
        self._stop_state_watcher()
        self._invalidate_route_callbacks()
        self._teardown_route()
        self._stop_ringing_pulse()
        self._stop_label_pulse()
        self._set_top_bar("ended")

        self.timer_label.hide()
        self._call_timer.stop()
        self._set_close_gate(True)
        self._set_route_panel_visible(False)
        self._set_extra_buttons(show_reply=False)
        state.set("call_origin", "unknown")
        state.set("call_muted", False)
        self._publish_state("ended", "phone")
        self._close_popup_now()

    def dismiss_active_call(self):
        """Tear down an active call session without surfacing an ended popup UI."""
        self.current_state = "ended"
        self._stop_state_watcher()
        self._invalidate_route_callbacks()
        self._teardown_route()
        self._stop_ringing_pulse()
        self._stop_label_pulse()
        self._call_timer.stop()
        self.timer_label.hide()
        self._set_close_gate(True)
        self._set_route_panel_visible(False)
        self._set_extra_buttons(show_reply=False)
        state.set("call_origin", "unknown")
        state.set("call_muted", False)
        self._publish_state("ended", "phone")
        self._close_popup_now()

    def try_close(self):
        if self.current_state in {"ringing", "talking"}:
            return
        self.hide_popup()

    def request_close(self):
        self.hide_popup()

    def answer_call(self):
        self.primary_btn.setEnabled(False)
        self.secondary_btn.setEnabled(False)
        state.set("call_local_end_action", "")
        state.set("call_origin", "popup_answer_laptop")
        ADBBridge().answer_call()
        self._enter_talking(reset_timer=True)
        self.primary_btn.setEnabled(True)
        self.secondary_btn.setEnabled(True)

    def reject_call(self):
        state.set("call_local_end_action", "reject")
        ADBBridge().end_call()
        self.dismiss_active_call()

    def end_call(self):
        state.set("call_local_end_action", "end")
        ADBBridge().end_call()
        self.dismiss_active_call()

    def toggle_mute(self):
        desired = not bool(state.get("call_muted", False))
        result = call_controls.set_call_muted(desired)
        self.secondary_btn.setChecked(bool(state.get("call_muted", False)))
        self.secondary_btn.setStyleSheet(self._button_style("mute", active=bool(state.get("call_muted", False))))
        try:
            from backend.ui_feedback import push_toast

            if result.ok:
                push_toast("Muted" if desired else "Unmuted", "info", 1300)
            else:
                push_toast(
                    "Mute failed on laptop route" if result.route == "laptop" else "Mute command unsupported on this Android build",
                    "warning",
                    1800,
                )
        except Exception:
            log.debug("Failed pushing mute toast", exc_info=True)

    def call_back(self):
        number = (self.current_number or "").strip()
        if number:
            state.set("call_local_end_action", "")
            state.set("call_origin", "unknown")
            ADBBridge()._run(
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.CALL",
                "-d",
                f"tel:{number}",
            )
        self._is_outbound_call = True
        self._enter_ringing()

    def dismiss_missed(self):
        self._publish_state("ended", "phone")
        self.hide_popup()

    def sms_reply_diversion_flow(self):
        self._call_timer.stop()
        if self.current_state == "talking":
            ADBBridge().end_call()
        self._teardown_route()
        state.set("sms_draft_number", self.current_number or "")
        self._publish_state("ended", "phone")

        if self.parent_window and hasattr(self.parent_window, "go_to"):
            self.parent_window.go_to("messages")

        try:
            from backend.ui_feedback import push_toast

            push_toast("Opening SMS composer…", "info", 1500)
        except Exception:
            log.debug("Failed pushing SMS diversion toast", exc_info=True)
        self.hide_popup()

    def _teardown_route(self, *, suspend_ui_global: bool = False):
        self._route_busy = False
        self._routed_to_pc = False
        audio_route.set_source("call_pc_active", False)
        suspend = bool(suspend_ui_global)
        threading.Thread(
            target=lambda: audio_route.sync_result(call_retry_ms=0, suspend_ui_global=suspend),
            daemon=True,
            name="pb-route-teardown",
        ).start()
        self._set_phone_selected(reset_failure=False)

    def _release_bt_call_route(self) -> bool:
        try:
            from backend.bluetooth_manager import BluetoothManager

            mgr = BluetoothManager()
            if not mgr.available():
                return False
            hints = [
                self.current_contact,
                self.current_number,
                settings.get("device_name", ""),
                "nothing",
                "phone",
                "a059",
            ]
            changed, _ = mgr.release_call_audio_route(hints, force_disconnect=True)
            return bool(changed)
        except Exception:
            return False

    def set_route_phone(self):
        log.info("set_route_phone: was_routed_to_pc=%s state=%s", self._routed_to_pc or self._route_busy, self.current_state)
        route_ui = state.get("call_route_ui_state", {}) or {}
        route_status = str(route_ui.get("status") or "phone").strip().lower()
        if (
            self.current_state in {"ringing", "talking"}
            and not self._routed_to_pc
            and not self._route_busy
            and route_status == "phone"
            and not bool(state.get("call_audio_active", False))
        ):
            log.debug("set_route_phone: already on phone route; skipping duplicate teardown")
            return
        was_routed_to_pc = self._routed_to_pc or self._route_busy
        self._invalidate_route_callbacks()
        self._teardown_route(suspend_ui_global=True)
        if was_routed_to_pc:
            threading.Thread(target=self._release_bt_call_route, daemon=True, name="pb-bt-release").start()
        self._animate_bt_panel(False)
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ended", "phone")
        log.info("set_route_phone: route teardown complete")
        try:
            from backend.ui_feedback import push_toast

            push_toast("Audio routed to phone", "info", 1300)
        except Exception:
            log.debug("Failed pushing route-to-phone toast", exc_info=True)

    def set_route_laptop(self):
        if self.current_state not in {"ringing", "talking"} or self._route_busy:
            return
        self._set_laptop_pending()
        self._animate_bt_panel(True)
        self._route_busy = True
        self._route_token_counter += 1
        token = (self._call_session_token, self._route_token_counter)
        self._active_route_token = token
        self._route_watchdog_token = token
        self._route_watchdog.start(18000)

        for row in self.bt_rows:
            row.set_state("pending")
            row.setVisible(True)

        self._route_worker = BTRouteWorker(
            preferred_name=self.current_contact or self.current_number,
            auto_connect=bool(settings.get("auto_bt_connect", True)),
            parent=self,
        )
        self._route_worker.step_update.connect(lambda idx, state_name, t=token: self._on_bt_step_update(t, idx, state_name))
        self._route_worker.route_success.connect(lambda t=token: self._on_bt_route_success(t))
        self._route_worker.route_failed.connect(lambda reason, sub_reason, t=token: self._on_bt_route_failed(t, reason, sub_reason))
        self._route_worker.finished.connect(lambda w=self._route_worker, t=token: self._on_bt_worker_finished(w, t))
        self._route_worker.start()

        state.set("call_route_status", "pending_pc")
        state.set("call_route_reason", "Preparing laptop call audio...")
        state.set("call_route_backend", "none")
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ringing", "pending_pc")

    def _set_phone_selected(self, *, reset_failure: bool):
        self.phone_option.set_mode(selected=True)
        self.laptop_option.set_mode(selected=False, failed=False, subtext="BT req." if reset_failure else None)

    def _set_laptop_pending(self):
        self.phone_option.set_mode(selected=False)
        self.laptop_option.set_mode(selected=True, failed=False, subtext="checking…")

    def _on_bt_step_update(self, token: tuple[int, int], idx: int, state_name: str):
        if self._route_callback_stale(token) or idx < 0 or idx >= len(self.bt_rows):
            return
        self.bt_rows[idx].set_state(state_name)

    def _on_bt_route_success(self, token: tuple[int, int]):
        if self._route_callback_stale(token):
            if self.current_state not in {"ringing", "talking"}:
                audio_route.set_source("call_pc_active", False)
                audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
            return
        log.info("set_route_laptop: route success token=%s state=%s", token, self.current_state)
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = True
        self.phone_option.set_mode(selected=False)
        self.laptop_option.set_mode(selected=True, subtext="ready")
        self._animate_bt_panel(False)
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "talking", "pc")
        state.set("call_route_status", "pc_active")
        state.set("call_route_reason", "Audio on laptop/PC")
        state.set("call_route_backend", "external_bt")
        try:
            from backend.ui_feedback import push_toast

            push_toast("Audio routed to laptop ✓", "success", 1500)
        except Exception:
            log.debug("Failed pushing route success toast", exc_info=True)

    def _on_bt_route_failed(self, token: tuple[int, int], reason: str, sub_reason: str):
        if self._route_callback_stale(token):
            if self.current_state not in {"ringing", "talking"}:
                audio_route.set_source("call_pc_active", False)
                audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
            return
        log.warning(
            "set_route_laptop: route failed token=%s state=%s reason=%s sub_reason=%s",
            token,
            self.current_state,
            reason,
            sub_reason,
        )
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = False
        self.phone_option.set_mode(selected=True)
        self.laptop_option.set_mode(selected=False, failed=True, subtext=sub_reason)
        self._animate_bt_panel(False)
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ringing", "phone")
        state.set("call_route_status", "pc_failed")
        state.set("call_route_reason", reason)
        state.set("call_route_backend", "none")
        try:
            from backend.ui_feedback import push_toast

            push_toast(reason, "warning", 1800)
        except Exception:
            log.debug("Failed pushing route failure toast", exc_info=True)

    def _on_bt_worker_finished(self, worker: BTRouteWorker | None, token: tuple[int, int]):
        if worker is None:
            return
        log.debug("set_route_laptop: worker finished token=%s active=%s", token, self._active_route_token == token)
        if (self._active_route_token is not None) and (token == self._active_route_token):
            self._route_busy = False
            self._invalidate_route_callbacks()
        if self._route_worker is worker:
            self._route_worker = None
        try:
            worker.deleteLater()
        except Exception:
            log.debug("Failed deleting BT route worker", exc_info=True)

    def _on_route_watchdog_timeout(self):
        token = self._route_watchdog_token
        if self._route_callback_stale(token):
            return
        log.warning("set_route_laptop: watchdog timeout token=%s state=%s", token, self.current_state)
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = False
        audio_route.set_source("call_pc_active", False)
        audio_route.sync_result(call_retry_ms=0, suspend_ui_global=True)
        self.phone_option.set_mode(selected=True)
        self.laptop_option.set_mode(selected=False, failed=True, subtext="Timed out")
        self._animate_bt_panel(False)
        state.set("call_route_status", "pc_failed")
        state.set("call_route_reason", "Laptop audio route timed out")
        state.set("call_route_backend", "none")
        self._publish_state(self.current_state if self.current_state in {"ringing", "talking"} else "ended", "phone")
        try:
            from backend.ui_feedback import push_toast

            push_toast("Laptop audio route timed out", "warning", 1800)
        except Exception:
            log.debug("Failed pushing route timeout toast", exc_info=True)

    def closeEvent(self, event):
        if (not self._allow_close) and self.current_state in {"ringing", "talking"} and event.spontaneous():
            event.ignore()
            return
        self._stop_state_watcher()
        self._route_watchdog.stop()
        worker = self._route_worker
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(800)
            except Exception:
                log.debug("Failed waiting for BT route worker interruption", exc_info=True)
            try:
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(300)
            except Exception:
                log.debug("Failed terminating BT route worker", exc_info=True)
            self._route_worker = None
        self._allow_close = True
        super().closeEvent(event)
        self._allow_close = False

    def hideEvent(self, event):
        self._stop_state_watcher()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.current_state in {"ringing", "talking"}:
            event.ignore()
            return
        super().keyPressEvent(event)
