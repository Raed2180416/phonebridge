"""Runtime mixin for PhoneBridgeWindow call and notification behavior."""

from __future__ import annotations

import logging
import os
import re
import threading
import time

from PyQt6.QtCore import QTimer

from backend import audio_route
from backend.call_routing import (
    build_call_route_ui_state,
    finalize_pending_call_session,
    is_redundant_live_call_event,
    meaningful_call_display_name,
    normalize_call_event,
    notification_reason_can_synthesize,
    outbound_origin_active,
    phone_match_key,
    plan_polled_call_state,
    reduce_call_session,
    resolve_call_display_name,
)
from backend.notification_mirror import sync_desktop_notifications
from backend.notifications_state import (
    normalize_notifications,
    phone_call_notification_key,
    record_dismissed_many,
    record_hidden_call_keys,
)
from backend.state import state
from backend.syncthing import Syncthing
import backend.settings_store as settings

log = logging.getLogger(__name__)


class WindowRuntimeMixin:
    def _ensure_runtime_async_state(self):
        if not hasattr(self, "_runtime_async_lock"):
            self._runtime_async_lock = threading.Lock()
        if not hasattr(self, "_audio_route_sync_busy"):
            self._audio_route_sync_busy = False
        if not hasattr(self, "_audio_route_sync_pending"):
            self._audio_route_sync_pending = False
        if not hasattr(self, "_audio_route_sync_pending_suspend"):
            self._audio_route_sync_pending_suspend = False
        if not hasattr(self, "_call_state_poll_busy"):
            self._call_state_poll_busy = False
        if not hasattr(self, "_call_state_poll_pending"):
            self._call_state_poll_pending = False
        if not hasattr(self, "_notif_sync_busy"):
            self._notif_sync_busy = False
        if not hasattr(self, "_notif_sync_pending"):
            self._notif_sync_pending = False
        return self._runtime_async_lock

    @staticmethod
    def _call_identity_fingerprint(number: str, display_name: str = "") -> str:
        key = phone_match_key(number)
        if key:
            return f"num:{key}"
        name = re.sub(r"\s+", " ", str(display_name or "").strip().lower())
        return f"name:{name}" if name else ""

    @staticmethod
    def _current_notif_revision_meta() -> tuple[str, int]:
        row = state.get("notif_revision", {}) or {}
        return str(row.get("id") or ""), int(row.get("updated_at") or 0)

    @staticmethod
    def _extract_candidate_phone(*parts: object) -> str:
        for part in parts:
            text = str(part or "").strip()
            if not text:
                continue
            match = re.search(r"\+?\d(?:[\d\s\-().]{5,}\d)?", text)
            if match:
                return match.group(0).strip()
        return ""

    @classmethod
    def _notification_call_row_details(cls, row: dict | None) -> dict[str, object]:
        payload = dict(row or {})
        title = str(payload.get("title") or "").strip()
        text = str(payload.get("text") or "").strip()
        actions = payload.get("actions") or []
        actions_blob = " ".join(str(a) for a in actions).lower()
        title_l = title.lower()
        text_l = text.lower()
        full_l = f"{title_l} {text_l}".strip()
        dismiss_kw = (
            "missed", "ended", "declined", "rejected", "cancelled",
            "canceled", "hang up", "hung up", "unanswered", "voicemail",
            "call ended", "call back",
        )
        action_kw = ("answer", "decline", "reject", "hang up", "accept")
        text_kw = (
            "incoming call", "is calling", "calling…", "calling...",
            "ringing", "call from", "phone call", "inbound call", "calling you",
        )
        if any(k in full_l for k in dismiss_kw):
            return {}
        has_answer_actions = any(k in actions_blob for k in action_kw)
        has_incoming_text = any(k in full_l for k in text_kw)
        if not has_answer_actions and not has_incoming_text:
            return {}

        candidate_name = title or text or "Incoming call"
        if candidate_name.lower() in {"phone", "incoming call", "notification", "dialer"}:
            candidate_name = (text or "Incoming call").strip()
        return {
            "name": candidate_name.strip() or "Incoming call",
            "number": cls._extract_candidate_phone(title, text),
            "strong_hint": bool(has_answer_actions),
            "notif_id": str(payload.get("id") or "").strip(),
            "row": payload,
        }

    @classmethod
    def _notification_call_candidate(cls, rows: list[dict] | None) -> dict[str, object]:
        for row in rows or []:
            candidate = cls._notification_call_row_details(row)
            if candidate:
                return candidate
        return {}

    def _remember_terminal_notification_guard(self, session) -> None:
        if session is None:
            return
        self._last_terminal_call_fingerprint = self._call_identity_fingerprint(
            getattr(session, "number", ""),
            getattr(session, "display_name", ""),
        )
        notif_id, notif_updated_at = self._current_notif_revision_meta()
        candidate = self._notification_call_candidate(state.get("notifications", []) or [])
        candidate_name = str(candidate.get("name") or "")
        candidate_notif_id = str(candidate.get("notif_id") or "")
        if (
            self._last_terminal_call_fingerprint
            and candidate_name
            and self._call_identity_fingerprint("", candidate_name) == self._last_terminal_call_fingerprint
            and candidate_notif_id
        ):
            notif_id = candidate_notif_id
        self._last_terminal_notification_id = notif_id
        self._last_terminal_notification_updated_at = int(notif_updated_at or 0)
        self._terminal_idle_boundary_open = False
        self._awaiting_terminal_idle_boundary = True

    def _clear_terminal_notification_guard(self) -> None:
        self._last_terminal_call_fingerprint = ""
        self._last_terminal_notification_id = ""
        self._last_terminal_notification_updated_at = 0

    def _reset_polled_live_candidate(self) -> None:
        self._polled_live_candidate_state = ""
        self._polled_live_candidate_hits = 0
        self._polled_live_candidate_first_at = 0.0
        self._polled_live_candidate_last_at = 0.0

    def _observe_polled_live_state(self, call_state: str, *, now_s: float) -> None:
        state_name = str(call_state or "").strip().lower()
        if state_name in {"ringing", "offhook"}:
            current_state = str(getattr(self, "_polled_live_candidate_state", "") or "")
            last_at = float(getattr(self, "_polled_live_candidate_last_at", 0.0) or 0.0)
            hits = int(getattr(self, "_polled_live_candidate_hits", 0) or 0)
            if current_state == state_name and (now_s - last_at) <= 4.5:
                self._polled_live_candidate_hits = hits + 1
            else:
                self._polled_live_candidate_state = state_name
                self._polled_live_candidate_hits = 1
                self._polled_live_candidate_first_at = now_s
            self._polled_live_candidate_last_at = now_s
            return
        if state_name == "idle":
            self._reset_polled_live_candidate()
            if bool(getattr(self, "_awaiting_terminal_idle_boundary", False)):
                self._terminal_idle_boundary_open = True
                self._awaiting_terminal_idle_boundary = False
            return
        last_at = float(getattr(self, "_polled_live_candidate_last_at", 0.0) or 0.0)
        if last_at and (now_s - last_at) > 6.0:
            self._reset_polled_live_candidate()

    def _polled_state_is_corroborated(self, call_state: str, *, now_s: float) -> bool:
        state_name = str(call_state or "").strip().lower()
        candidate_state = str(getattr(self, "_polled_live_candidate_state", "") or "")
        if state_name != candidate_state:
            return False
        hits = int(getattr(self, "_polled_live_candidate_hits", 0) or 0)
        first_at = float(getattr(self, "_polled_live_candidate_first_at", 0.0) or 0.0)
        last_at = float(getattr(self, "_polled_live_candidate_last_at", 0.0) or 0.0)
        if hits < 2 or not first_at or not last_at:
            return False
        return (now_s - first_at) <= 5.5 and (now_s - last_at) <= 4.5

    def _polled_ringing_edge_can_open_session(self, *, previous_non_unknown_state: str) -> bool:
        current = getattr(self, "_call_session_state", None)
        if current is not None and str(getattr(current, "phase", "") or "").strip().lower() in {"dialing", "ringing", "talking"}:
            return False
        if not bool(getattr(self, "_terminal_idle_boundary_open", True)):
            return False
        return str(previous_non_unknown_state or "unknown").strip().lower() != "ringing"

    def _session_should_finalize_from_idle(self, *, now_s: float) -> bool:
        current = getattr(self, "_call_session_state", None)
        if current is None:
            return False
        if str(getattr(current, "pending_terminal", "") or "").strip():
            return False
        phase = str(getattr(current, "phase", "") or "").strip().lower()
        if phase not in {"dialing", "ringing", "talking"}:
            return False
        updated_at_ms = int(getattr(current, "updated_at_ms", 0) or 0)
        if updated_at_ms <= 0:
            return False
        age_ms = max(0, int(now_s * 1000) - updated_at_ms)
        guard_ms = 4_500 if phase == "dialing" else (2_500 if phase == "ringing" else 2_000)
        return age_ms >= guard_ms

    def _adb_can_promote_to_talking(self, *, now_s: float) -> bool:
        current = getattr(self, "_call_session_state", None)
        phase = str(getattr(current, "phase", "") or "").strip().lower()
        if phase not in {"dialing", "ringing"}:
            return False
        return self._polled_state_is_corroborated("offhook", now_s=now_s)

    def _notification_row_matches_call(self, row: dict | None, number: str, display_name: str) -> bool:
        payload = dict(row or {})
        details = self._notification_call_row_details(payload)
        title = str(payload.get("title") or "").strip()
        text = str(payload.get("text") or "").strip()
        call_key = phone_match_key(number)
        if call_key:
            extracted = str(details.get("number") or "") or self._extract_candidate_phone(title, text)
            if phone_match_key(extracted) == call_key:
                return True
        wanted_name = meaningful_call_display_name(display_name, number)
        if not wanted_name:
            return False
        wanted_key = re.sub(r"\s+", " ", wanted_name).strip().lower()
        haystacks = [
            str(details.get("name") or "").strip().lower(),
            title.lower(),
            text.lower(),
        ]
        return any(hay and wanted_key in hay for hay in haystacks)

    def _hide_terminal_call_notifications(self, session) -> None:
        if session is None:
            return
        hidden_ids: list[str] = []
        hidden_keys: list[str] = []
        for row in list(state.get("notifications", []) or []):
            if not self._notification_row_matches_call(row, getattr(session, "number", ""), getattr(session, "display_name", "")):
                continue
            notif_id = str((row or {}).get("id") or "").strip()
            call_key = phone_call_notification_key(row)
            if notif_id:
                hidden_ids.append(notif_id)
            if call_key:
                hidden_keys.append(call_key)
        if hidden_ids:
            hidden_ids_set = set(hidden_ids)
            record_dismissed_many(hidden_ids)
            state.update(
                "notifications",
                lambda rows: [row for row in list(rows or []) if str((row or {}).get("id") or "").strip() not in hidden_ids_set],
                default=[],
            )
        if hidden_keys:
            record_hidden_call_keys(hidden_keys)

    def _warm_call_popup_surface(self) -> None:
        try:
            popup = self._ensure_call_popup()
            if hasattr(popup, "warmup_surface"):
                popup.warmup_surface()
                log.info("Call popup surface warmup complete")
        except Exception:
            log.debug("Call popup surface warmup failed", exc_info=True)

    def _arm_poll_popup_fallback(self, number: str, contact_name: str) -> None:
        self._pending_poll_popup = {
            "number": str(number or "").strip(),
            "contact_name": str(contact_name or "").strip(),
            "armed_at_ms": int(time.time() * 1000),
        }
        self._poll_popup_fallback_timer.start(900)

    def _cancel_poll_popup_fallback(self) -> None:
        self._pending_poll_popup = None
        self._poll_popup_fallback_timer.stop()

    def _fire_pending_poll_popup_fallback(self) -> None:
        payload = dict(getattr(self, "_pending_poll_popup", None) or {})
        self._pending_poll_popup = None
        if not payload:
            return
        now = time.time()
        current_polled = str(self._last_polled_call_state or "").strip().lower()
        recent_non_unknown = str(getattr(self, "_last_non_unknown_polled_call_state", "") or "").strip().lower()
        recent_non_unknown_age = now - float(getattr(self, "_last_non_unknown_polled_at", 0.0) or 0.0)
        effective_polled = current_polled
        if effective_polled not in {"ringing", "offhook"} and recent_non_unknown in {"ringing", "offhook"} and recent_non_unknown_age < 3.5:
            effective_polled = recent_non_unknown
        if effective_polled not in {"ringing", "offhook"}:
            return
        current = getattr(self, "_call_session_state", None)
        if current is not None and str(getattr(current, "phase", "") or "").strip().lower() in {"ringing", "talking"}:
            return
        number = str(payload.get("number") or "").strip()
        contact_name = str(payload.get("contact_name") or "").strip() or "Incoming call"
        log.info(
            "ADB delayed call popup fallback firing state=%s number=%s contact=%s",
            effective_polled,
            number or "(unknown)",
            contact_name,
        )
        self._on_call_received("talking" if effective_polled == "offhook" else "incoming_call", number, contact_name)

    def _current_call_audio_target(self) -> str:
        route_ui = state.get("call_route_ui_state", {}) or {}
        status = str(route_ui.get("status") or "phone").strip().lower()
        if status == "pending":
            return "pending_pc"
        if status == "laptop":
            return "pc"
        return "phone"

    def _sync_call_route_ui_state_from_state(self) -> None:
        state.set(
            "call_route_ui_state",
            build_call_route_ui_state(
                route_status=str(state.get("call_route_status", "phone") or "phone"),
                route_reason=str(state.get("call_route_reason", "") or ""),
                route_backend=str(state.get("call_route_backend", "none") or "none"),
                call_audio_active=bool(state.get("call_audio_active", False)),
                call_muted=bool(state.get("call_muted", False)),
                updated_at_ms=int(time.time() * 1000),
            ),
        )

    @staticmethod
    def _dedupe_call_contacts(rows: list[dict] | None) -> list[dict]:
        seen: set[str] = set()
        deduped: list[dict] = []
        for row in rows or []:
            phone = str(row.get("phone") or row.get("number") or "").strip()
            key = phone_match_key(phone) or phone
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(
                {
                    "name": str(row.get("name") or row.get("display_name") or phone).strip() or phone,
                    "phone": phone,
                }
            )
        return deduped

    def _prime_call_contacts_cache_async(self) -> None:
        if bool(getattr(self, "_call_contacts_cache_loading", False)):
            return
        self._call_contacts_cache_loading = True

        def _job():
            try:
                rows = []
                try:
                    from backend.kdeconnect import KDEConnect

                    rows = KDEConnect().get_cached_contacts() or []
                except Exception:
                    log.debug("Call contacts prime via KDE cache failed", exc_info=True)
                if not rows:
                    try:
                        adb = getattr(self, "_adb", None)
                        if adb is None:
                            from backend.adb_bridge import ADBBridge

                            adb = ADBBridge()
                        rows = adb.get_contacts(limit=500) or []
                    except Exception:
                        log.debug("Call contacts prime via ADB failed", exc_info=True)
                deduped = self._dedupe_call_contacts(rows)
                if deduped:
                    state.set("call_contacts_cache", deduped)
                    log.info("Primed call contacts cache rows=%s", len(deduped))
            finally:
                self._call_contacts_cache_loading = False

        threading.Thread(target=_job, daemon=True, name="pb-call-contacts-prime").start()

    def _call_contacts_cache(self) -> list[dict]:
        cached = list(state.get("call_contacts_cache", []) or [])
        if cached:
            return cached
        self._prime_call_contacts_cache_async()
        return []

    @staticmethod
    def _call_history_cache() -> list[dict]:
        return list(state.get("recent_calls_cache", []) or [])

    def _prime_pending_terminal_recent_calls_async(self) -> None:
        current = getattr(self, "_call_session_state", None)
        if current is None or not str(getattr(current, "pending_terminal", "") or "").strip():
            return
        token = int(getattr(self, "_pending_terminal_recent_calls_token", 0) or 0) + 1
        self._pending_terminal_recent_calls_token = token

        def _job():
            rows: list[dict] = []
            try:
                adb = getattr(self, "_adb", None)
                if adb is None:
                    from backend.adb_bridge import ADBBridge

                    adb = ADBBridge()
                rows = list(adb.get_recent_calls(limit=20) or [])
            except Exception:
                log.debug("Pending terminal recent-calls fetch failed", exc_info=True)
            if token != int(getattr(self, "_pending_terminal_recent_calls_token", 0) or 0):
                return
            self._pending_terminal_recent_calls = rows
            if rows:
                state.set("recent_calls_cache", rows)

        threading.Thread(target=_job, daemon=True, name="pb-terminal-calls").start()

    def _resolve_call_display_name(self, number: str, explicit_name: str, *, previous_name: str = "") -> str:
        raw_number = str(number or "").strip()
        explicit = str(explicit_name or "").strip()
        previous = str(previous_name or "").strip()
        if not phone_match_key(raw_number):
            if explicit:
                return explicit
            if previous:
                return previous
            return "Incoming call"
        if meaningful_call_display_name(explicit, raw_number):
            return explicit
        outbound = state.get("outbound_call_origin", {}) or {}
        contacts = self._call_contacts_cache()
        return resolve_call_display_name(
            number,
            explicit_name,
            contacts=contacts,
            recent_calls=self._call_history_cache(),
            outbound_number=str(outbound.get("number") or ""),
            outbound_display_name=str(outbound.get("display_name") or outbound.get("number") or ""),
            previous_display_name=previous_name,
        )

    def _publish_call_session(self, session) -> None:
        if session is None:
            return
        state.set_many(
            {
                "call_state": {
                    "event": session.phase,
                    "number": session.number,
                    "contact_name": session.display_name,
                },
                "call_ui_state": session.to_public_row(),
            }
        )

    def _ensure_call_popup(self, *, force_new: bool = False):
        if force_new and self._call_popup is not None:
            try:
                self._call_popup.close()
            except Exception:
                log.debug("Failed closing previous call popup", exc_info=True)
            self._call_popup = None
        if self._call_popup is None:
            from ui.components.call_popup import CallPopup

            self._call_popup = CallPopup(None)
            log.info("Call popup pre-created (first time)")
        self._call_popup.set_parent_window(self if self.isVisible() else None)
        return self._call_popup

    def _register_hyprland_popup_rules(self):
        try:
            from backend.system_integration import ensure_hyprland_call_popup_rules

            ok, msg = ensure_hyprland_call_popup_rules()
            log.info("Hyprland call-popup rules pre-registered at startup: %s", msg)
            if not ok:
                log.debug("Hyprland popup rule pre-registration reported degraded state")
        except Exception:
            log.debug("Hyprland call-popup rules pre-registration failed (non-Hyprland?)", exc_info=True)

    def _update_call_popup_position(self):
        popup = self._call_popup
        if popup is None:
            return
        popup.set_parent_window(self if self.isVisible() else None)
        if popup.isVisible():
            popup.update_position()

    def _publish_call_snapshot(
        self,
        status: str,
        number: str,
        contact_name: str,
        audio_target: str = "phone",
        *,
        source: str = "verification",
    ):
        display_name = self._resolve_call_display_name(number, contact_name, previous_name=contact_name)
        now_ms = int(time.time() * 1000)
        session = self._call_session_state
        decision = reduce_call_session(
            session,
            raw_event=status,
            number=number,
            display_name=display_name,
            origin=str(state.get("call_origin", "unknown") or "unknown"),
            audio_target=audio_target,
            now_ms=now_ms,
            source=source,
        )
        self._apply_call_session_decision(decision)

    def _sync_calls_page_call(self, normalized_event: str, number: str, contact_name: str):
        calls_page = self.get_page("calls")
        if calls_page and hasattr(calls_page, "add_call"):
            calls_page.add_call(normalized_event, number, contact_name)

    @staticmethod
    def _set_call_origin(origin: str) -> None:
        value = str(origin or "unknown").strip() or "unknown"
        if value not in {"calls_page_outbound", "popup_answer_laptop", "phone_answer", "unknown"}:
            value = "unknown"
        state.set("call_origin", value)

    def _finalize_pending_call_terminal(self):
        current = self._call_session_state
        if current is None or not getattr(current, "pending_terminal", ""):
            return
        recent_calls = list(getattr(self, "_pending_terminal_recent_calls", []) or []) or self._call_history_cache()
        local_end_action = str(state.get("call_local_end_action", "") or "").strip().lower()
        if not recent_calls:
            log.info(
                "Finalizing pending call terminal from cached state only terminal=%s number=%s local_end_action=%s",
                getattr(current, "pending_terminal", ""),
                getattr(current, "number", ""),
                local_end_action or "(none)",
            )
        decision = finalize_pending_call_session(
            current,
            now_ms=int(time.time() * 1000),
            recent_calls=recent_calls,
            local_end_action=local_end_action,
        )
        self._pending_terminal_recent_calls = []
        self._pending_terminal_recent_calls_token = 0
        self._apply_call_session_decision(decision)

    def _apply_call_session_decision(self, decision) -> None:
        started = time.perf_counter()
        session = getattr(decision, "session", None)
        if getattr(decision, "clear_terminal_check", False):
            self._call_terminal_timer.stop()
            self._pending_terminal_recent_calls = []
            self._pending_terminal_recent_calls_token = 0
        if getattr(decision, "schedule_terminal_check_ms", 0):
            self._call_terminal_timer.start(int(decision.schedule_terminal_check_ms))
            self._prime_pending_terminal_recent_calls_async()
        if getattr(decision, "ignored", False) and session is self._call_session_state:
            return

        self._call_session_state = session
        if getattr(decision, "publish", False) and session is not None:
            phase = str(getattr(session, "phase", "") or "").strip().lower()
            if phase in {"ringing", "talking", "dialing"} and not str(getattr(session, "pending_terminal", "") or "").strip():
                self._clear_terminal_notification_guard()
                self._terminal_idle_boundary_open = True
                self._awaiting_terminal_idle_boundary = False
                state.set("call_local_end_action", "")
            elif phase in {"ended", "missed_call"}:
                self._remember_terminal_notification_guard(session)
                self._hide_terminal_call_notifications(session)
                state.set("call_local_end_action", "")
            self._publish_call_session(session)
            popup = self._call_popup
            if popup is not None and not getattr(decision, "popup_event", ""):
                try:
                    popup.update_call_context(session.phase, session.number, session.display_name)
                except Exception:
                    log.debug("Failed updating active popup call context", exc_info=True)
        popup_event = str(getattr(decision, "popup_event", "") or "").strip().lower()
        if popup_event and session is not None:
            if popup_event == "ended":
                self._cancel_poll_popup_fallback()
                popup = self._call_popup
                if popup is not None:
                    try:
                        popup.dismiss_active_call()
                    except Exception:
                        log.exception("Failed dismissing popup for ended call")
                log.info(
                    "Call decision popup_event=ended dismissed without popup total_dt_ms=%.1f",
                    (time.perf_counter() - started) * 1000.0,
                )
            elif popup_event == "missed_call" and not bool(settings.get("missed_call_popups_enabled", True)):
                popup = self._call_popup
                if popup is not None and getattr(popup, "is_popup_active", lambda: popup.isVisible())():
                    popup.dismiss_active_call()
            elif not (popup_event in {"ringing", "talking"} and bool(settings.get("suppress_calls", False))):
                try:
                    popup = self._ensure_call_popup()
                    popup_started = time.perf_counter()
                    popup.handle_call_event(session.number, session.display_name, popup_event)
                    log.info(
                        "Call decision popup_event=%s applied dt_ms=%.1f total_dt_ms=%.1f",
                        popup_event,
                        (time.perf_counter() - popup_started) * 1000.0,
                        (time.perf_counter() - started) * 1000.0,
                    )
                except Exception:
                    log.exception("Failed applying popup session event=%s", popup_event)

        if getattr(decision, "history_event", "") and session is not None:
            QTimer.singleShot(
                0,
                lambda event=decision.history_event, number=session.number, name=session.display_name: self._sync_calls_page_call(
                    event,
                    number,
                    name,
                ),
            )
        if not popup_event or session is None:
            return
        if popup_event == "missed_call" and not bool(settings.get("missed_call_popups_enabled", True)):
            return
        if popup_event in {"ringing", "talking"} and bool(settings.get("suppress_calls", False)):
            return

    def _on_call_received(self, event, number, contact_name, *, source: str = "signal"):
        started = time.perf_counter()
        source_key = str(source or "signal").strip().lower()
        if source_key in {"signal", "trusted"}:
            source_role = "signal"
        elif source_key in {"user", "user_action"}:
            source_role = "user_action"
        elif source_key in {"telephony_poll", "poll_signal"}:
            source_role = "telephony_poll"
        else:
            source_role = source_key
        raw_event_key = str(event or "").strip().lower().replace("-", "_")
        if raw_event_key in {"rejected", "declined"}:
            state.set("call_local_end_action", "reject")
        normalized_event = normalize_call_event(event)
        now = time.time()
        self._cancel_poll_popup_fallback()
        previous_session = self._call_session_state
        dedupe_key = (
            str(source or "signal").strip().lower(),
            normalized_event,
            phone_match_key(number) or str(number or "").strip(),
            str(contact_name or "").strip(),
        )
        if dedupe_key == getattr(self, "_last_call_key", "") and (now - float(getattr(self, "_last_call_at", 0.0) or 0.0)) < 1.2:
            log.debug(
                "Dropping duplicate call event source=%s raw_event=%s normalized_event=%s number=%s contact=%s",
                source,
                event,
                normalized_event,
                number,
                contact_name,
            )
            return
        self._last_call_key = dedupe_key
        self._last_call_at = now
        log.info(
            "Signal callReceived source=%s raw_event=%s normalized_event=%s number=%s contact=%s",
            source,
            event,
            normalized_event,
            number,
            contact_name,
        )
        outbound_origin = state.get("outbound_call_origin", {}) or {}
        outbound_active = outbound_origin_active(outbound_origin, now_ms=int(time.time() * 1000))
        if normalized_event == "ringing" and not outbound_active:
            audio_route.set_source("call_pc_active", False)
            self._set_call_origin("unknown")
        elif normalized_event == "ringing" and outbound_active:
            self._set_call_origin("calls_page_outbound")

        if normalized_event == "talking":
            if outbound_active:
                self._set_call_origin("calls_page_outbound")
            else:
                origin = str(state.get("call_origin", "unknown") or "unknown")
                if origin not in {"popup_answer_laptop", "calls_page_outbound"}:
                    self._set_call_origin("phone_answer")
            state.set("outbound_call_origin", {})
        display_name = self._resolve_call_display_name(
            number,
            contact_name,
            previous_name=(previous_session.display_name if previous_session is not None else ""),
        )
        log.debug(
            "Call event resolved display_name=%s event=%s dt_ms=%.1f",
            display_name,
            normalized_event,
            (time.perf_counter() - started) * 1000.0,
        )
        decision = reduce_call_session(
            previous_session,
            raw_event=event,
            number=number,
            display_name=display_name,
            origin=str(state.get("call_origin", "unknown") or "unknown"),
            audio_target=self._current_call_audio_target(),
            now_ms=int(now * 1000),
            source=source,
        )
        effective_phase = normalized_event
        if getattr(decision, "session", None) is not None:
            effective_phase = str(decision.session.phase or normalized_event).strip().lower()
        if hasattr(self, "_call_controller") and self._call_controller is not None:
            self._call_controller.note_signal_event(effective_phase)
        if effective_phase in {"ringing", "talking"}:
            self._suspend_poll_until = now + 0.9
        should_sync_route = (
            source_role in {"signal", "user_action", "telephony_poll"}
            and normalized_event in {"ringing", "talking"}
            and not is_redundant_live_call_event(previous_session, raw_event=normalized_event, number=number)
            and not bool(getattr(decision, "ignored", False))
        )
        if should_sync_route:
            self._sync_audio_route_async(suspend_ui_global=True)
        elif normalized_event in {"ringing", "talking"} and getattr(decision, "ignored", False):
            log.debug(
                "Skipping audio-route sync for ignored live call event event=%s number=%s",
                normalized_event,
                number,
            )
        log.debug(
            "Applying call decision event=%s phase=%s popup_event=%s ignored=%s total_dt_ms=%.1f",
            normalized_event,
            str(getattr(getattr(decision, "session", None), "phase", "") or ""),
            str(getattr(decision, "popup_event", "") or ""),
            bool(getattr(decision, "ignored", False)),
            (time.perf_counter() - started) * 1000.0,
        )
        self._apply_call_session_decision(decision)
        if source_role in {"signal", "telephony_poll"} and normalized_event == "ringing":
            QTimer.singleShot(0, lambda: self._maybe_synthesize_call_from_notifications(trigger_reason="signal"))

    def _on_notif_changed(self, notif_payload):
        if isinstance(notif_payload, dict):
            notif_id = str(notif_payload.get("id") or "")
            reason = str(notif_payload.get("reason") or "unknown").strip().lower()
        else:
            notif_id = str(notif_payload or "")
            reason = "unknown"
        log.info("Signal notification changed id=%s reason=%s", notif_id, reason)
        state.set(
            "notif_revision",
            {"id": str(notif_id), "reason": reason, "updated_at": int(time.time() * 1000)},
        )
        self._sync_notification_mirror_snapshot()
        if notification_reason_can_synthesize(reason):
            QTimer.singleShot(220, lambda r=reason: self._maybe_synthesize_call_from_notifications(trigger_reason=r))
        messages_page = self.get_page("messages")
        call_ui = state.get("call_ui_state", {}) or {}
        call_phase = str(call_ui.get("phase") or call_ui.get("status") or "").strip().lower()
        if messages_page and hasattr(messages_page, "refresh") and call_phase not in {"ringing", "talking"}:
            QTimer.singleShot(120, messages_page.refresh)

    def _maybe_synthesize_call_from_notifications(self, *, trigger_reason: str = "unknown"):
        if not notification_reason_can_synthesize(trigger_reason):
            return
        current_session = getattr(self, "_call_session_state", None)
        if current_session is None:
            return
        if str(getattr(current_session, "phase", "") or "").strip().lower() != "ringing":
            return
        candidate = self._notification_call_candidate(list(state.get("notifications", []) or []))
        if not candidate:
            return

        candidate_name = str(candidate.get("name") or "")
        candidate_number = str(candidate.get("number") or "").strip()
        current_name = meaningful_call_display_name(
            getattr(current_session, "display_name", ""),
            getattr(current_session, "number", ""),
        )
        candidate_display_name = meaningful_call_display_name(
            candidate_name,
            candidate_number or getattr(current_session, "number", ""),
        )
        current_number_key = phone_match_key(getattr(current_session, "number", ""))
        candidate_number_key = phone_match_key(candidate_number)
        current_has_meaningful_name = bool(current_name)
        should_update_name = bool(candidate_display_name) and not current_has_meaningful_name
        should_update_number = bool(candidate_number_key) and candidate_number_key != current_number_key
        if current_number_key and candidate_number_key and candidate_number_key != current_number_key:
            return
        if current_has_meaningful_name and not should_update_number:
            return
        if not should_update_name and not should_update_number:
            return

        log.info(
            "Enriching active ringing session from notification caller=%s number=%s reason=%s",
            candidate_name or "(unknown)",
            candidate_number or "(unknown)",
            trigger_reason,
        )
        self._publish_call_snapshot(
            "ringing",
            candidate_number or getattr(current_session, "number", ""),
            candidate_display_name or candidate_name or getattr(current_session, "display_name", ""),
            self._current_call_audio_target(),
            source="notification",
        )

    def _on_notif_open_request(self, payload):
        row = dict(payload or {})
        notif_id = str(row.get("id") or "")
        source = str(row.get("source") or "desktop_notification")
        log.info("Notification open request id=%s source=%s", notif_id, source)
        self.show_and_raise(reason=f"notif_open:{source}:{notif_id or 'unknown'}")
        self.go_to("messages")

    def _on_syncthing_runtime_status(self, _payload):
        call_ui = state.get("call_ui_state", {}) or {}
        if str(call_ui.get("phase") or call_ui.get("status") or "").strip().lower() in {"ringing", "talking"}:
            return
        current = getattr(self, "_stack", None)
        if current is None:
            return
        try:
            page_container = current.currentWidget()
            page = page_container.widget() if hasattr(page_container, "widget") else page_container
        except Exception:
            log.debug("Syncthing runtime status refresh skipped: current page unavailable", exc_info=True)
            return
        if page is None or not hasattr(page, "refresh"):
            return
        if not bool(getattr(page, "allow_runtime_status_refresh", True)):
            return
        QTimer.singleShot(120, page.refresh)

    def _mirror_stream_running(self) -> bool:
        get_page = getattr(self, "get_page", None)
        if not callable(get_page):
            return False
        mirror = get_page("mirror")
        if mirror and hasattr(mirror, "is_mirror_stream_running"):
            try:
                return bool(mirror.is_mirror_stream_running())
            except Exception:
                return False
        return False

    def _sync_audio_route_async(self, *, suspend_ui_global: bool):
        next_suspend = bool(suspend_ui_global)
        with self._ensure_runtime_async_state():
            if self._audio_route_sync_busy:
                self._audio_route_sync_pending = True
                self._audio_route_sync_pending_suspend = bool(
                    self._audio_route_sync_pending_suspend or next_suspend
                )
                return
            self._audio_route_sync_busy = True

        def _job():
            try:
                audio_route.sync(suspend_ui_global=next_suspend)
            except Exception:
                log.exception("Async audio-route sync failed")
            finally:
                rerun = False
                rerun_suspend = False
                with self._ensure_runtime_async_state():
                    self._audio_route_sync_busy = False
                    rerun = bool(self._audio_route_sync_pending)
                    rerun_suspend = bool(self._audio_route_sync_pending_suspend)
                    self._audio_route_sync_pending = False
                    self._audio_route_sync_pending_suspend = False
                if rerun:
                    self._sync_audio_route_async(suspend_ui_global=rerun_suspend)

        threading.Thread(target=_job, daemon=True, name="pb-audio-sync").start()

    def _poll_phone_call_state_async(self):
        with self._ensure_runtime_async_state():
            if self._call_state_poll_busy:
                self._call_state_poll_pending = True
                return
        if time.time() < self._suspend_poll_until:
            return
        with self._ensure_runtime_async_state():
            self._call_state_poll_busy = True

        def _job():
            try:
                call_state = str(self._adb.get_call_state_fast() or "unknown").lower()
            except Exception as exc:
                log.warning("Call poll thread exception: %s", exc)
                call_state = "unknown"
            self._call_state_ready.emit(call_state)

        threading.Thread(target=_job, daemon=True, name="pb-call-poll").start()

    def _apply_polled_call_state(self, call_state: str):
        rerun_poll = False
        with self._ensure_runtime_async_state():
            self._call_state_poll_busy = False
        try:
            now_s = time.time()
            previous_non_unknown_state = str(getattr(self, "_last_non_unknown_polled_call_state", "unknown") or "unknown")
            plan = plan_polled_call_state(
                call_state,
                previous_state=self._last_polled_call_state,
                route_suspended=self._call_state_route_suspended,
                call_ui=state.get("call_ui_state", {}) or {},
                suppress_calls=bool(settings.get("suppress_calls", False)),
                now_s=now_s,
            )
            if hasattr(self, "_call_controller") and self._call_controller is not None:
                self._call_controller.note_polled_state(plan.call_state)
            if plan.state_changed:
                log.info("Polled phone call state=%s", plan.call_state)
                self._last_polled_call_state = plan.call_state
                self._last_polled_at = now_s
                if plan.call_state != "unknown":
                    self._last_non_unknown_polled_call_state = plan.call_state
                    self._last_non_unknown_polled_at = self._last_polled_at
            self._observe_polled_live_state(plan.call_state, now_s=now_s)
            if plan.should_synthesize_from_notifications:
                self._maybe_synthesize_call_from_notifications(trigger_reason="poll")
                return

            if plan.sync_audio_suspend:
                self._sync_audio_route_async(suspend_ui_global=True)
            elif plan.sync_audio_restore and self._call_state_route_suspended:
                self._sync_audio_route_async(suspend_ui_global=self._mirror_stream_running())
            self._call_state_route_suspended = plan.next_route_suspended

            if plan.call_state == "ringing" and self._polled_ringing_edge_can_open_session(previous_non_unknown_state=previous_non_unknown_state):
                contact_name = str(plan.contact_name or "").strip()
                if not str(plan.number or "").strip() and (not contact_name or contact_name.lower() == "unknown"):
                    contact_name = "Incoming call"
                if bool(settings.get("suppress_calls", False)):
                    self._publish_call_snapshot("ringing", plan.number, contact_name, "phone", source="telephony_poll")
                else:
                    self._on_call_received("incoming_call", plan.number, contact_name, source="telephony_poll")
            elif plan.call_state == "idle" and self._session_should_finalize_from_idle(now_s=now_s):
                current = getattr(self, "_call_session_state", None)
                self._cancel_poll_popup_fallback()
                self._on_call_received(
                    "ended",
                    str(getattr(current, "number", "") or ""),
                    str(getattr(current, "display_name", "") or ""),
                    source="verification",
                )
            elif plan.action == "ended":
                self._cancel_poll_popup_fallback()
                self._on_call_received("ended", plan.number, plan.contact_name, source="verification")
        finally:
            with self._ensure_runtime_async_state():
                if self._call_state_poll_pending and time.time() >= self._suspend_poll_until:
                    self._call_state_poll_pending = False
                    rerun_poll = True
            if rerun_poll:
                QTimer.singleShot(0, self._poll_phone_call_state_async)

    def _sync_notification_mirror_snapshot(self):
        with self._ensure_runtime_async_state():
            if self._notif_sync_busy:
                self._notif_sync_pending = True
                return
        if not settings.get("kde_integration_enabled", True):
            sync_desktop_notifications([])
            return
        with self._ensure_runtime_async_state():
            self._notif_sync_busy = True

        def _job():
            try:
                from backend.kdeconnect import KDEConnect

                rows = normalize_notifications(KDEConnect().get_notifications())
                state.set("notifications", rows)
                sync_desktop_notifications(rows)
            except Exception:
                log.exception("Notification mirror snapshot sync failed")
            finally:
                rerun = False
                with self._ensure_runtime_async_state():
                    self._notif_sync_busy = False
                    rerun = bool(self._notif_sync_pending)
                    self._notif_sync_pending = False
                if rerun:
                    self._sync_notification_mirror_snapshot()

        threading.Thread(target=_job, daemon=True).start()

    @staticmethod
    def _enforce_notification_popup_policy():
        try:
            from backend.kdeconnect import KDEConnect

            KDEConnect.suppress_native_notification_popups(True)
        except Exception:
            log.exception("Failed applying KDE notification popup policy")
