"""Pure call routing helpers (no Qt/DBus dependencies).

Used both by runtime (ui/window.py) and optional deterministic tests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
import time
from typing import Any


def normalize_call_event(event: str) -> str:
    e = (event or "").strip().lower().replace("-", "_")
    if "missed" in e:
        return "missed_call"
    if e in {"ended", "end", "hangup", "disconnected", "idle", "terminated", "declined", "rejected"}:
        return "ended"
    if e in {"ringing", "callreceived", "incoming", "incoming_call"}:
        return "ringing"
    if e in {"talking", "answered", "in_call", "ongoing", "active", "callstarted"}:
        return "talking"
    return e or "ringing"


def outbound_origin_active(origin: dict[str, Any] | None, *, now_ms: int | None = None) -> bool:
    row = origin or {}
    if row.get("source") != "calls_page":
        return False
    if not bool(row.get("active")):
        return False
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    ts_ms = int(row.get("ts_ms", 0) or 0)
    return (now_ms - ts_ms) < 75_000


def should_suppress_popup(normalized_event: str, origin: dict[str, Any] | None, *, now_ms: int | None = None) -> bool:
    if normalized_event not in {"ringing", "talking"}:
        return False
    return outbound_origin_active(origin, now_ms=now_ms)


def notification_reason_can_synthesize(reason: str) -> bool:
    """Whether a notification change reason can trigger incoming-call synthesis."""
    value = str(reason or "").strip().lower()
    if not value:
        return True
    return value in {"posted", "updated", "poll", "unknown", "startup"}


def should_attempt_notification_call_synthesis(
    call_ui: dict[str, Any] | None,
    *,
    now_ms: int,
    pending_terminal: str = "",
    active_guard_ms: int = 8_000,
    terminal_guard_ms: int = 12_000,
) -> bool:
    """Whether notification hints are safe to use for call synthesis.

    Notification-driven synthesis is a fallback for missing telephony signals.
    It should not reopen or mutate a call session that is already active or has
    just landed in a terminal state.
    """

    row = dict(call_ui or {})
    phase = str(row.get("phase") or row.get("status") or "").strip().lower()
    updated_at_ms = int(row.get("updated_at") or 0)
    age_ms = max(0, int(now_ms) - updated_at_ms) if updated_at_ms > 0 else 1_000_000

    if pending_terminal and age_ms < int(terminal_guard_ms):
        return False
    if phase in {"dialing", "ringing", "talking"} and age_ms < int(active_guard_ms):
        return False
    if phase in {"missed_call", "ended"} and age_ms < int(terminal_guard_ms):
        return False
    return True


def allow_call_hint_when_recent_idle(polled_state: str, polled_age_s: float, *, strong_hint: bool, idle_guard_s: float = 6.0) -> bool:
    """Guard against stale ringing synthesis from old notifications while idle."""
    state = str(polled_state or "").strip().lower()
    age = float(polled_age_s or 0.0)
    if state == "idle" and age < float(idle_guard_s) and not bool(strong_hint):
        return False
    return True


GENERIC_DISPLAY_NAMES = frozenset(
    {
        "",
        "unknown",
        "incoming call",
        "phone",
        "dialer",
        "call",
        "notification",
        "missed call",
    }
)


def normalize_phone_number(number: str) -> str:
    raw = str(number or "").strip()
    if not raw:
        return ""
    keep = []
    for idx, ch in enumerate(raw):
        if ch.isdigit():
            keep.append(ch)
        elif ch == "+" and idx == 0:
            keep.append(ch)
    return "".join(keep)


def phone_match_key(number: str) -> str:
    normalized = normalize_phone_number(number)
    digits = "".join(ch for ch in normalized if ch.isdigit())
    if not digits:
        return normalized
    return digits[-10:]


def is_redundant_live_call_event(
    current: "CallSessionState | None",
    *,
    raw_event: str,
    number: str,
) -> bool:
    """Whether a live signal is semantically redundant for route/UI work.

    KDE Connect can emit repeated ``talking`` signals for the same active
    session, and some devices transiently emit ``ringing`` again after the call
    is already active. Those events should still be allowed to refresh display
    names through the reducer, but they should not trigger another expensive
    audio-route sync cycle.
    """

    if current is None:
        return False
    event = normalize_call_event(raw_event)
    if event not in {"ringing", "talking"}:
        return False
    incoming_number = normalize_phone_number(number) or str(number or "").strip()
    if not _same_call_number(current.number, incoming_number):
        return False
    if current.pending_terminal:
        return False
    if current.phase == event:
        return True
    return current.phase == "talking" and event == "ringing"


def _meaningful_display_name(name: str, number: str = "") -> str:
    value = str(name or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered in GENERIC_DISPLAY_NAMES:
        return ""
    if phone_match_key(value) and phone_match_key(value) == phone_match_key(number):
        return ""
    return value


def meaningful_call_display_name(name: str, number: str = "") -> str:
    """Public wrapper for validating caller names without exposing internals."""
    return _meaningful_display_name(name, number)


def _normalized_name_key(name: str, number: str = "", *, allow_generic: bool = False) -> str:
    value = str(name or "").strip()
    if not allow_generic:
        value = _meaningful_display_name(value, number)
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip().lower()


def _best_name_from_rows(number: str, rows: list[dict[str, Any]] | None, *, name_keys: tuple[str, ...]) -> str:
    wanted = phone_match_key(number)
    if not wanted:
        return ""
    for row in rows or []:
        row_number = str(row.get("phone") or row.get("number") or "").strip()
        if phone_match_key(row_number) != wanted:
            continue
        for key in name_keys:
            candidate = _meaningful_display_name(str(row.get(key) or "").strip(), number)
            if candidate:
                return candidate
    return ""


def resolve_call_display_name(
    number: str,
    explicit_name: str,
    *,
    contacts: list[dict[str, Any]] | None = None,
    recent_calls: list[dict[str, Any]] | None = None,
    outbound_number: str = "",
    outbound_display_name: str = "",
    previous_display_name: str = "",
) -> str:
    normalized_number = normalize_phone_number(number)
    explicit = _meaningful_display_name(explicit_name, normalized_number)
    if explicit:
        return explicit

    contact_name = _best_name_from_rows(normalized_number, contacts, name_keys=("name", "display_name"))
    if contact_name:
        return contact_name

    history_name = _best_name_from_rows(normalized_number, recent_calls, name_keys=("name", "contact_name"))
    if history_name:
        return history_name

    if phone_match_key(outbound_number) and phone_match_key(outbound_number) == phone_match_key(normalized_number):
        outbound_name = _meaningful_display_name(outbound_display_name, normalized_number)
        if outbound_name:
            return outbound_name

    previous = _meaningful_display_name(previous_display_name, normalized_number)
    if previous:
        return previous
    return normalized_number or str(number or "").strip() or "Unknown"


def build_call_route_ui_state(
    *,
    route_status: str,
    route_reason: str,
    route_backend: str,
    call_audio_active: bool,
    call_muted: bool,
    updated_at_ms: int,
) -> dict[str, Any]:
    status = str(route_status or "phone").strip().lower()
    reason = str(route_reason or "").strip()
    backend = str(route_backend or "none").strip().lower()
    active = bool(call_audio_active) or status == "pc_active"

    if status == "pending_pc":
        ui_status = "pending"
        speaker_target = "Laptop"
        mic_target = "Laptop"
        mute_available = False
    elif active:
        ui_status = "laptop"
        speaker_target = "Laptop"
        mic_target = "Laptop"
        mute_available = True
    elif status == "pc_failed":
        ui_status = "failed"
        speaker_target = "Phone"
        mic_target = "Phone"
        mute_available = False
    else:
        ui_status = "phone"
        speaker_target = "Phone"
        mic_target = "Phone"
        mute_available = False

    return {
        "status": ui_status,
        "speaker_target": speaker_target,
        "mic_target": mic_target,
        "reason": reason,
        "backend": backend,
        "mute_available": mute_available,
        "mute_active": bool(call_muted) if mute_available else False,
        "updated_at": int(updated_at_ms or 0),
    }


@dataclass(frozen=True)
class CallSessionState:
    session_id: int
    phase: str
    number: str
    display_name: str
    origin: str
    audio_target: str
    started_at_ms: int
    updated_at_ms: int
    pending_terminal: str = ""
    pending_since_ms: int = 0

    def to_public_row(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase,
            "status": self.phase,
            "number": self.number,
            "display_name": self.display_name,
            "contact_name": self.display_name,
            "origin": self.origin,
            "audio_target": self.audio_target,
            "updated_at": self.updated_at_ms,
        }


@dataclass(frozen=True)
class CallSessionDecision:
    session: CallSessionState | None
    popup_event: str = ""
    history_event: str = ""
    publish: bool = False
    schedule_terminal_check_ms: int = 0
    clear_terminal_check: bool = False
    ignored: bool = False


def _same_call_number(current_number: str, incoming_number: str) -> bool:
    current_key = phone_match_key(current_number)
    incoming_key = phone_match_key(incoming_number)
    if not current_key or not incoming_key:
        return False
    return current_key == incoming_key


def _same_live_call_identity(current: "CallSessionState", incoming_number: str, incoming_display_name: str) -> bool:
    if _same_call_number(current.number, incoming_number):
        return True
    current_name = _normalized_name_key(current.display_name, current.number)
    incoming_name = _normalized_name_key(incoming_display_name, incoming_number)
    if current_name and incoming_name:
        return current_name == incoming_name

    current_has_identity = bool(phone_match_key(current.number) or current_name)
    incoming_has_identity = bool(phone_match_key(incoming_number) or incoming_name)
    if not current_has_identity:
        return True
    return not incoming_has_identity


def _same_terminal_call_identity(current: "CallSessionState", incoming_number: str, incoming_display_name: str) -> bool:
    if _same_call_number(current.number, incoming_number):
        return True
    current_name = _normalized_name_key(current.display_name, current.number)
    incoming_name = _normalized_name_key(incoming_display_name, incoming_number)
    if current_name and incoming_name:
        return current_name == incoming_name
    return False


def _session_origin(origin: str) -> str:
    value = str(origin or "unknown").strip() or "unknown"
    if value not in {"calls_page_outbound", "popup_answer_laptop", "phone_answer", "unknown"}:
        return "unknown"
    return value


def _source_class(source: str) -> str:
    value = str(source or "").strip().lower()
    if value in {"signal", "trusted"}:
        return "signal"
    if value in {"telephony_poll", "poll_signal"}:
        return "telephony_poll"
    if value in {"user", "user_action"}:
        return "user_action"
    if value == "notification":
        return "notification"
    return "verification"


def seed_outbound_call_session(
    number: str,
    display_name: str,
    *,
    now_ms: int,
    origin: str = "calls_page_outbound",
    audio_target: str = "pending_pc",
) -> CallSessionState:
    clean_number = normalize_phone_number(number) or str(number or "").strip()
    clean_name = resolve_call_display_name(clean_number, display_name, previous_display_name=display_name)
    return CallSessionState(
        session_id=max(1, int(now_ms)),
        phase="dialing",
        number=clean_number,
        display_name=clean_name,
        origin=_session_origin(origin),
        audio_target=str(audio_target or "phone"),
        started_at_ms=max(1, int(now_ms)),
        updated_at_ms=max(1, int(now_ms)),
    )


def _new_call_session(
    *,
    event: str,
    number: str,
    display_name: str,
    origin: str,
    audio_target: str,
    now_ms: int,
) -> CallSessionDecision:
    session = CallSessionState(
        session_id=now_ms,
        phase=event,
        number=number,
        display_name=display_name or number or "Unknown",
        origin=origin,
        audio_target=audio_target,
        started_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    return CallSessionDecision(
        session=session,
        popup_event=event,
        history_event=event,
        publish=True,
        clear_terminal_check=True,
    )


def reduce_call_session(
    current: CallSessionState | None,
    *,
    raw_event: str,
    number: str,
    display_name: str,
    origin: str,
    audio_target: str,
    now_ms: int,
    source: str = "signal",
) -> CallSessionDecision:
    event = normalize_call_event(raw_event)
    number = normalize_phone_number(number) or str(number or "").strip()
    origin = _session_origin(origin)
    audio_target = str(audio_target or "phone")
    now_ms = max(1, int(now_ms))
    source_class = _source_class(source)
    authoritative_source = source_class in {"signal", "user_action", "telephony_poll"}

    if current is None:
        if event == "ended":
            return CallSessionDecision(session=None, ignored=True, clear_terminal_check=True)
        if not authoritative_source:
            return CallSessionDecision(session=None, ignored=True)
        return _new_call_session(
            event=event,
            number=number,
            display_name=display_name,
            origin=origin,
            audio_target=audio_target,
            now_ms=now_ms,
        )

    if current.phase in {"missed_call", "ended"}:
        same_call = _same_terminal_call_identity(current, number, display_name)
    else:
        same_call = _same_live_call_identity(current, number, display_name)
    if (not same_call) and event in {"ringing", "talking", "missed_call"}:
        if not authoritative_source:
            return CallSessionDecision(session=current, ignored=True)
        return _new_call_session(
            event=event,
            number=number,
            display_name=display_name,
            origin=origin,
            audio_target=audio_target,
            now_ms=now_ms,
        )

    if same_call and event == current.phase and display_name == current.display_name and not current.pending_terminal:
        return CallSessionDecision(session=current, ignored=True)

    updated = replace(
        current,
        number=number or current.number,
        display_name=display_name or current.display_name,
        origin=origin if origin != "unknown" else current.origin,
        audio_target=audio_target or current.audio_target,
        updated_at_ms=now_ms,
    )

    if current.phase in {"missed_call", "ended"}:
        if event in {"ringing", "talking", "missed_call"} and same_call:
            if not authoritative_source:
                return CallSessionDecision(session=current, ignored=True)
            return _new_call_session(
                event=event,
                number=number or current.number,
                display_name=display_name or current.display_name,
                origin=origin if origin != "unknown" else current.origin,
                audio_target=audio_target or current.audio_target,
                now_ms=now_ms,
            )
        if event in {"missed_call", "ended"} and same_call:
            terminal = event if event == "missed_call" else current.phase
            updated = replace(updated, phase=terminal, pending_terminal="", pending_since_ms=0)
            return CallSessionDecision(session=updated, publish=True, clear_terminal_check=True)
        return CallSessionDecision(session=current, ignored=True)

    if event == "ringing":
        if not authoritative_source and current.phase not in {"ringing", "talking"}:
            return CallSessionDecision(session=current, ignored=True)
        phase = "ringing" if current.phase != "talking" else "talking"
        updated = replace(updated, phase=phase, pending_terminal="", pending_since_ms=0)
        emit_live_popup = authoritative_source and current.phase not in {"ringing", "talking"}
        return CallSessionDecision(
            session=updated,
            popup_event="ringing" if emit_live_popup else "",
            history_event="ringing" if emit_live_popup else "",
            publish=True,
            clear_terminal_check=True,
        )

    if event == "talking":
        if not authoritative_source and current.phase != "talking":
            return CallSessionDecision(session=current, ignored=True)
        updated = replace(
            updated,
            phase="talking" if authoritative_source or current.phase == "talking" else current.phase,
            pending_terminal="",
            pending_since_ms=0,
        )
        return CallSessionDecision(
            session=updated,
            popup_event="talking" if authoritative_source and current.phase != "talking" else "",
            history_event="talking" if authoritative_source and current.phase != "talking" else "",
            publish=True,
            clear_terminal_check=True,
        )

    if event == "missed_call":
        if not authoritative_source:
            return CallSessionDecision(session=current, ignored=True, clear_terminal_check=True)
        if current.phase in {"dialing", "ringing"}:
            # Some Android/KDE stacks emit a premature missed-call signal while
            # the call is still actively ringing. Record the hint, but do not
            # surface a terminal popup until the session actually ends or the
            # recent-call log corroborates it.
            updated = replace(
                updated,
                pending_terminal="missed_call",
                pending_since_ms=now_ms,
            )
            return CallSessionDecision(
                session=updated,
                publish=True,
            )
        return CallSessionDecision(session=updated, ignored=True, clear_terminal_check=True)

    if event == "ended":
        if current.phase in {"dialing", "ringing"}:
            terminal_hint = "missed_call" if current.pending_terminal == "missed_call" else "ended"
            updated = replace(updated, pending_terminal=terminal_hint, pending_since_ms=now_ms)
            return CallSessionDecision(
                session=updated,
                publish=True,
                schedule_terminal_check_ms=900,
            )
        updated = replace(updated, phase="ended", audio_target="phone", pending_terminal="", pending_since_ms=0)
        return CallSessionDecision(
            session=updated,
            popup_event="ended",
            history_event="ended",
            publish=True,
            clear_terminal_check=True,
        )

    return CallSessionDecision(session=updated, ignored=True)


def infer_terminal_event_from_recent_calls(
    number: str,
    recent_calls: list[dict[str, Any]] | None,
    *,
    now_ms: int,
    recent_window_ms: int = 180_000,
) -> str:
    wanted = phone_match_key(number)
    if not wanted:
        return ""
    for row in recent_calls or []:
        row_number = str(row.get("number") or row.get("phone") or "").strip()
        if phone_match_key(row_number) != wanted:
            continue
        date_ms = int(row.get("date_ms") or 0)
        if date_ms and abs(int(now_ms) - date_ms) > int(recent_window_ms):
            continue
        event = normalize_call_event(str(row.get("event") or ""))
        if event == "missed_call":
            return "missed_call"
        if event in {"ended", "rejected"}:
            return "ended"
    return ""


def finalize_pending_call_session(
    current: CallSessionState | None,
    *,
    now_ms: int,
    recent_calls: list[dict[str, Any]] | None = None,
    local_end_action: str = "",
) -> CallSessionDecision:
    if current is None or not current.pending_terminal:
        return CallSessionDecision(session=current, ignored=True, clear_terminal_check=True)
    terminal = infer_terminal_event_from_recent_calls(current.number, recent_calls, now_ms=now_ms)
    if not terminal:
        local_action = str(local_end_action or "").strip().lower()
        if local_action in {"reject", "end"}:
            terminal = "ended"
        elif current.pending_terminal == "missed_call":
            terminal = "missed_call"
        else:
            terminal = current.pending_terminal or "ended"
    updated = replace(
        current,
        phase=terminal,
        audio_target="phone",
        updated_at_ms=max(1, int(now_ms)),
        pending_terminal="",
        pending_since_ms=0,
    )
    return CallSessionDecision(
        session=updated,
        popup_event=terminal,
        history_event=terminal,
        publish=True,
        clear_terminal_check=True,
    )


@dataclass(frozen=True)
class PolledCallPlan:
    """Pure plan for how the UI should react to an ADB-polled call state."""

    call_state: str
    state_changed: bool
    next_route_suspended: bool
    should_synthesize_from_notifications: bool
    sync_audio_suspend: bool
    sync_audio_restore: bool
    action: str
    number: str
    contact_name: str


def plan_polled_call_state(
    call_state: str,
    *,
    previous_state: str,
    route_suspended: bool,
    call_ui: dict[str, Any] | None,
    suppress_calls: bool,
    now_s: float | None = None,
    stale_after_ms: int = 8_000,
    dialing_idle_guard_ms: int = 4_500,
    ringing_idle_guard_ms: int = 2_500,
    talking_idle_guard_ms: int = 2_000,
) -> PolledCallPlan:
    """Return the deterministic UI/audio plan for a polled telephony state."""

    normalized = str(call_state or "unknown").strip().lower()
    if normalized not in {"idle", "ringing", "offhook", "unknown"}:
        normalized = "unknown"

    row = dict(call_ui or {})
    ui_status = str(row.get("phase") or row.get("status") or "").strip().lower()
    ui_number = str(row.get("number") or "")
    ui_name = str(row.get("display_name") or row.get("contact_name") or ui_number)
    ui_updated_ms = int(row.get("updated_at") or 0)
    now_s = float(time.time() if now_s is None else now_s)
    ui_age_ms = max(0, int(now_s * 1000) - ui_updated_ms) if ui_updated_ms > 0 else 1_000_000
    action = ""

    if normalized == "unknown":
        return PolledCallPlan(
            call_state=normalized,
            state_changed=normalized != str(previous_state or "unknown").strip().lower(),
            next_route_suspended=bool(route_suspended),
            should_synthesize_from_notifications=should_attempt_notification_call_synthesis(
                row,
                now_ms=int(now_s * 1000),
            ),
            sync_audio_suspend=False,
            sync_audio_restore=False,
            action="",
            number=ui_number,
            contact_name=ui_name,
        )

    next_route_suspended = bool(route_suspended)
    sync_audio_suspend = False
    sync_audio_restore = False

    if normalized in {"ringing", "offhook"}:
        sync_audio_suspend = True
        next_route_suspended = True
    else:
        if route_suspended:
            sync_audio_restore = True
        next_route_suspended = False
        if ui_status == "dialing" and ui_age_ms < int(dialing_idle_guard_ms):
            action = ""
        elif ui_status == "ringing" and ui_age_ms < int(ringing_idle_guard_ms):
            action = ""
        elif ui_status == "talking" and ui_age_ms < int(talking_idle_guard_ms):
            action = ""
        elif ui_status in {"dialing", "ringing", "talking"}:
            action = "ended"

    return PolledCallPlan(
        call_state=normalized,
        state_changed=normalized != str(previous_state or "unknown").strip().lower(),
        next_route_suspended=next_route_suspended,
        should_synthesize_from_notifications=False,
        sync_audio_suspend=sync_audio_suspend,
        sync_audio_restore=sync_audio_restore,
        action=action,
        number=ui_number,
        contact_name=ui_name,
    )
