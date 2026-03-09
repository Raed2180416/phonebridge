"""Deterministic coverage for call-session reduction and route UI state."""

from __future__ import annotations

from backend.call_routing import (
    build_call_route_ui_state,
    finalize_pending_call_session,
    is_redundant_live_call_event,
    plan_polled_call_state,
    reduce_call_session,
    resolve_call_display_name,
    should_attempt_notification_call_synthesis,
)


def test_route_ui_state_maps_phone_pending_laptop_and_failed_variants():
    assert build_call_route_ui_state(
        route_status="phone",
        route_reason="",
        route_backend="none",
        call_audio_active=False,
        call_muted=False,
        updated_at_ms=1,
    ) == {
        "status": "phone",
        "speaker_target": "Phone",
        "mic_target": "Phone",
        "reason": "",
        "backend": "none",
        "mute_available": False,
        "mute_active": False,
        "updated_at": 1,
    }

    pending = build_call_route_ui_state(
        route_status="pending_pc",
        route_reason="Preparing laptop call audio...",
        route_backend="none",
        call_audio_active=False,
        call_muted=False,
        updated_at_ms=2,
    )
    assert pending["status"] == "pending"
    assert pending["speaker_target"] == "Laptop"
    assert pending["mic_target"] == "Laptop"
    assert pending["mute_available"] is False

    laptop = build_call_route_ui_state(
        route_status="pc_active",
        route_reason="Audio on laptop/PC",
        route_backend="external_bt",
        call_audio_active=True,
        call_muted=True,
        updated_at_ms=3,
    )
    assert laptop["status"] == "laptop"
    assert laptop["mute_available"] is True
    assert laptop["mute_active"] is True

    failed = build_call_route_ui_state(
        route_status="pc_failed",
        route_reason="Laptop route failed",
        route_backend="none",
        call_audio_active=False,
        call_muted=True,
        updated_at_ms=4,
    )
    assert failed["status"] == "failed"
    assert failed["speaker_target"] == "Phone"
    assert failed["mic_target"] == "Phone"
    assert failed["mute_available"] is False
    assert failed["mute_active"] is False


def test_notification_call_synthesis_is_blocked_for_recent_terminal_or_active_sessions():
    now_ms = 50_000
    assert should_attempt_notification_call_synthesis(
        {"phase": "missed_call", "updated_at": now_ms - 2_000},
        now_ms=now_ms,
    ) is False
    assert should_attempt_notification_call_synthesis(
        {"phase": "ended", "updated_at": now_ms - 3_000},
        now_ms=now_ms,
    ) is False
    assert should_attempt_notification_call_synthesis(
        {"phase": "talking", "updated_at": now_ms - 2_000},
        now_ms=now_ms,
    ) is False
    assert should_attempt_notification_call_synthesis(
        {"phase": "ended", "updated_at": now_ms - 30_000},
        now_ms=now_ms,
    ) is True
    assert should_attempt_notification_call_synthesis(
        {"phase": "idle", "updated_at": now_ms - 1_000},
        now_ms=now_ms,
        pending_terminal="missed_call",
    ) is False


def test_unknown_polled_state_does_not_reopen_recent_terminal_call_via_notifications():
    plan = plan_polled_call_state(
        "unknown",
        previous_state="idle",
        route_suspended=False,
        call_ui={
            "phase": "missed_call",
            "status": "missed_call",
            "updated_at": 9_000,
            "number": "+15551234567",
            "contact_name": "Alice",
        },
        suppress_calls=False,
        now_s=10.0,
    )
    assert plan.should_synthesize_from_notifications is False


def test_display_name_resolution_prefers_explicit_then_contacts_then_history_then_outbound():
    number = "+1 (555) 123-4567"
    contacts = [{"name": "Alice Contact", "phone": "5551234567"}]
    recent_calls = [{"contact_name": "Alice History", "number": "15551234567"}]

    assert resolve_call_display_name(number, "Alice Signal") == "Alice Signal"
    assert resolve_call_display_name(number, "Incoming Call", contacts=contacts) == "Alice Contact"
    assert resolve_call_display_name(number, "", contacts=[], recent_calls=recent_calls) == "Alice History"
    assert (
        resolve_call_display_name(
            number,
            "",
            contacts=[],
            recent_calls=[],
            outbound_number="5551234567",
            outbound_display_name="Alice Outbound",
        )
        == "Alice Outbound"
    )
    assert resolve_call_display_name(number, "", contacts=[], recent_calls=[]) == "+15551234567"


def test_incoming_ringing_finalizes_to_missed_call_without_ended_flash():
    first = reduce_call_session(
        None,
        raw_event="ringing",
        number="+1 555 123 4567",
        display_name="Incoming Call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    )
    assert first.popup_event == "ringing"
    assert first.session is not None

    ended = reduce_call_session(
        first.session,
        raw_event="ended",
        number="+15551234567",
        display_name="Incoming Call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_800,
    )
    assert ended.popup_event == ""
    assert ended.schedule_terminal_check_ms == 900
    assert ended.session is not None
    assert ended.session.phase == "ringing"
    assert ended.session.pending_terminal == "ended"

    finalized = finalize_pending_call_session(
        ended.session,
        now_ms=2_100,
        recent_calls=[{"event": "missed_call", "number": "5551234567", "date_ms": 2_000}],
    )
    assert finalized.session is not None
    assert finalized.session.phase == "missed_call"
    assert finalized.popup_event == "missed_call"


def test_incoming_ringing_without_missed_evidence_finalizes_to_silent_ended():
    first = reduce_call_session(
        None,
        raw_event="ringing",
        number="+1 555 123 4567",
        display_name="Incoming Call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    )
    assert first.session is not None

    ended = reduce_call_session(
        first.session,
        raw_event="ended",
        number="+15551234567",
        display_name="Incoming Call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_800,
    )
    assert ended.session is not None
    assert ended.session.pending_terminal == "ended"

    finalized = finalize_pending_call_session(
        ended.session,
        now_ms=2_100,
        recent_calls=[],
    )
    assert finalized.session is not None
    assert finalized.session.phase == "ended"
    assert finalized.popup_event == "ended"


def test_locally_rejected_incoming_ringing_finalizes_to_ended_not_missed():
    first = reduce_call_session(
        None,
        raw_event="ringing",
        number="+1 555 123 4567",
        display_name="Incoming Call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    )
    assert first.session is not None

    ended = reduce_call_session(
        first.session,
        raw_event="ended",
        number="+15551234567",
        display_name="Incoming Call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_800,
    )
    assert ended.session is not None
    assert ended.session.pending_terminal == "ended"

    finalized = finalize_pending_call_session(
        ended.session,
        now_ms=2_100,
        recent_calls=[],
        local_end_action="reject",
    )
    assert finalized.session is not None
    assert finalized.session.phase == "ended"
    assert finalized.popup_event == "ended"


def test_answered_call_finishes_as_ended_not_missed():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    talking = reduce_call_session(
        ringing,
        raw_event="talking",
        number="5551234567",
        display_name="Alice",
        origin="phone_answer",
        audio_target="pc",
        now_ms=2_000,
    ).session
    assert talking is not None
    assert talking.phase == "talking"

    ended = reduce_call_session(
        talking,
        raw_event="ended",
        number="5551234567",
        display_name="Alice",
        origin="phone_answer",
        audio_target="pc",
        now_ms=3_000,
    )
    assert ended.session is not None
    assert ended.session.phase == "ended"
    assert ended.popup_event == "ended"


def test_provisional_missed_call_hint_does_not_tear_down_active_session_before_talking():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="+91 98867 87942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None
    assert ringing.phase == "ringing"

    provisional_missed = reduce_call_session(
        ringing,
        raw_event="missed_call",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_100,
    )
    assert provisional_missed.session is not None
    assert provisional_missed.session.phase == "ringing"
    assert provisional_missed.session.pending_terminal == "missed_call"
    assert provisional_missed.popup_event == ""

    talking = reduce_call_session(
        provisional_missed.session,
        raw_event="talking",
        number="+919886787942",
        display_name="Mom",
        origin="phone_answer",
        audio_target="phone",
        now_ms=12_000,
    )
    assert talking.session is not None
    assert talking.session.phase == "talking"
    assert talking.session.pending_terminal == ""
    assert talking.popup_event == "talking"


def test_provisional_missed_hint_finalizes_to_missed_once_call_really_ends():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    missed_hint = reduce_call_session(
        ringing,
        raw_event="missed_call",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_200,
    ).session
    assert missed_hint is not None
    assert missed_hint.pending_terminal == "missed_call"

    ended = reduce_call_session(
        missed_hint,
        raw_event="ended",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
    )
    assert ended.session is not None
    assert ended.session.phase == "ringing"
    assert ended.session.pending_terminal == "missed_call"
    assert ended.popup_event == ""
    assert ended.schedule_terminal_check_ms == 900

    finalized = finalize_pending_call_session(
        ended.session,
        now_ms=2_300,
        recent_calls=[],
    )
    assert finalized.session is not None
    assert finalized.session.phase == "missed_call"
    assert finalized.popup_event == "missed_call"


def test_rejected_recent_call_overrides_pending_missed_hint_to_ended():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    missed_hint = reduce_call_session(
        ringing,
        raw_event="missed_call",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_200,
    ).session
    assert missed_hint is not None

    ended = reduce_call_session(
        missed_hint,
        raw_event="ended",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
    )
    assert ended.session is not None
    assert ended.session.pending_terminal == "missed_call"

    finalized = finalize_pending_call_session(
        ended.session,
        now_ms=2_300,
        recent_calls=[{"event": "rejected", "number": "5551234567", "date_ms": 2_100}],
    )
    assert finalized.session is not None
    assert finalized.session.phase == "ended"
    assert finalized.popup_event == "ended"


def test_local_reject_overrides_pending_missed_hint_to_ended():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    missed_hint = reduce_call_session(
        ringing,
        raw_event="missed_call",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_200,
    ).session
    assert missed_hint is not None

    ended = reduce_call_session(
        missed_hint,
        raw_event="ended",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
    )
    assert ended.session is not None
    assert ended.session.pending_terminal == "missed_call"

    finalized = finalize_pending_call_session(
        ended.session,
        now_ms=2_300,
        recent_calls=[],
        local_end_action="reject",
    )
    assert finalized.session is not None
    assert finalized.session.phase == "ended"
    assert finalized.popup_event == "ended"


def test_live_generic_session_merges_richer_identity_without_second_popup():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="",
        display_name="Incoming call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    enriched = reduce_call_session(
        ringing,
        raw_event="ringing",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_200,
    )
    assert enriched.session is not None
    assert enriched.session.session_id == ringing.session_id
    assert enriched.session.phase == "ringing"
    assert enriched.session.number == "+919886787942"
    assert enriched.session.display_name == "Mom"
    assert enriched.popup_event == ""


def test_terminal_generic_session_does_not_swallow_next_real_incoming_call():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="",
        display_name="Incoming call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    talking = reduce_call_session(
        ringing,
        raw_event="talking",
        number="",
        display_name="Incoming call",
        origin="phone_answer",
        audio_target="phone",
        now_ms=1_400,
    ).session
    assert talking is not None

    terminal = reduce_call_session(
        talking,
        raw_event="ended",
        number="",
        display_name="Incoming call",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
    ).session
    assert terminal is not None
    assert terminal.phase == "ended"

    next_call = reduce_call_session(
        terminal,
        raw_event="ringing",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=2_100,
    )
    assert next_call.session is not None
    assert next_call.session.session_id == 2_100
    assert next_call.session.phase == "ringing"
    assert next_call.session.number == "+919886787942"
    assert next_call.session.display_name == "Mom"
    assert next_call.popup_event == "ringing"


def test_terminal_same_number_verification_noise_cannot_reopen_after_guard_window():
    terminal = reduce_call_session(
        None,
        raw_event="missed_call",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
    ).session
    assert terminal is not None
    assert terminal.phase == "missed_call"

    reopened = reduce_call_session(
        terminal,
        raw_event="ringing",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=8_000,
        source="adb",
    )
    assert reopened.ignored is True
    assert reopened.session is not None
    assert reopened.session.session_id == terminal.session_id
    assert reopened.session.phase == "missed_call"


def test_trusted_signal_reopens_same_number_terminal_session_without_waiting_for_guard_window():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    talking = reduce_call_session(
        ringing,
        raw_event="talking",
        number="+919886787942",
        display_name="Mom",
        origin="phone_answer",
        audio_target="phone",
        now_ms=1_100,
    ).session
    assert talking is not None

    terminal = reduce_call_session(
        talking,
        raw_event="ended",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_200,
    ).session
    assert terminal is not None
    assert terminal.phase == "ended"

    reopened = reduce_call_session(
        terminal,
        raw_event="ringing",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_500,
        source="signal",
    )
    assert reopened.session is not None
    assert reopened.session.session_id == 1_500
    assert reopened.session.phase == "ringing"
    assert reopened.popup_event == "ringing"


def test_terminal_same_number_chatter_within_guard_is_ignored():
    terminal = reduce_call_session(
        None,
        raw_event="missed_call",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert terminal is not None

    duplicate = reduce_call_session(
        terminal,
        raw_event="ringing",
        number="+919886787942",
        display_name="Mom",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
        source="adb",
    )
    assert duplicate.ignored is True
    assert duplicate.session is not None
    assert duplicate.session.session_id == terminal.session_id


def test_verification_source_cannot_create_live_session_from_idle():
    ignored = reduce_call_session(
        None,
        raw_event="ringing",
        number="+15551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
        source="adb",
    )
    assert ignored.ignored is True
    assert ignored.session is None


def test_telephony_poll_source_can_create_live_session_from_idle():
    created = reduce_call_session(
        None,
        raw_event="ringing",
        number="",
        display_name="Incoming call",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
        source="telephony_poll",
    )
    assert created.ignored is False
    assert created.session is not None
    assert created.session.phase == "ringing"
    assert created.popup_event == "ringing"


def test_verification_source_cannot_promote_ringing_session_to_talking():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="+15551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
        source="signal",
    ).session
    assert ringing is not None

    ignored = reduce_call_session(
        ringing,
        raw_event="talking",
        number="+15551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_200,
        source="adb",
    )
    assert ignored.ignored is True
    assert ignored.session is not None
    assert ignored.session.phase == "ringing"


def test_terminal_session_ignores_late_reopen_noise_for_same_number():
    missed = reduce_call_session(
        None,
        raw_event="missed_call",
        number="5551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert missed is not None

    late_ringing = reduce_call_session(
        missed,
        raw_event="ringing",
        number="+1 555 123 4567",
        display_name="Alice Better Name",
        origin="unknown",
        audio_target="phone",
        now_ms=2_000,
        source="adb",
    )
    assert late_ringing.ignored is True
    assert late_ringing.session is not None
    assert late_ringing.session.phase == "missed_call"
    assert late_ringing.session.display_name == "Alice"


def test_redundant_live_call_event_detection_ignores_same_session_repeats():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="+15551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    assert is_redundant_live_call_event(ringing, raw_event="ringing", number="5551234567") is True
    assert is_redundant_live_call_event(ringing, raw_event="talking", number="5551234567") is False

    talking = reduce_call_session(
        ringing,
        raw_event="talking",
        number="5551234567",
        display_name="Alice",
        origin="phone_answer",
        audio_target="phone",
        now_ms=2_000,
    ).session
    assert talking is not None

    assert is_redundant_live_call_event(talking, raw_event="talking", number="+1 555 123 4567") is True
    assert is_redundant_live_call_event(talking, raw_event="ringing", number="+1 555 123 4567") is True
    assert is_redundant_live_call_event(talking, raw_event="talking", number="+1 555 999 9999") is False


def test_late_ringing_after_talking_does_not_emit_a_second_popup_transition():
    ringing = reduce_call_session(
        None,
        raw_event="ringing",
        number="+15551234567",
        display_name="Alice",
        origin="unknown",
        audio_target="phone",
        now_ms=1_000,
    ).session
    assert ringing is not None

    talking = reduce_call_session(
        ringing,
        raw_event="talking",
        number="+15551234567",
        display_name="Alice",
        origin="phone_answer",
        audio_target="phone",
        now_ms=2_000,
    ).session
    assert talking is not None
    assert talking.phase == "talking"

    late_ringing = reduce_call_session(
        talking,
        raw_event="ringing",
        number="+15551234567",
        display_name="Alice",
        origin="phone_answer",
        audio_target="phone",
        now_ms=2_100,
    )
    assert late_ringing.session is not None
    assert late_ringing.session.phase == "talking"
    assert late_ringing.popup_event == ""
    assert late_ringing.history_event == ""
