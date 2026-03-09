"""Deterministic tests for the polled-call planning seam."""

from __future__ import annotations

from backend.call_routing import plan_polled_call_state


def test_polled_call_plan_restores_audio_after_idle():
    plan = plan_polled_call_state(
        "idle",
        previous_state="offhook",
        route_suspended=True,
        call_ui={"status": "talking", "number": "+123", "contact_name": "Alice", "updated_at": 1_000},
        suppress_calls=False,
        now_s=10.0,
    )

    assert plan.sync_audio_restore is True
    assert plan.next_route_suspended is False
    assert plan.action == "ended"


def test_polled_call_plan_treats_offhook_as_verification_only():
    plan = plan_polled_call_state(
        "offhook",
        previous_state="idle",
        route_suspended=False,
        call_ui={"status": "idle", "number": "+123", "contact_name": "Alice", "updated_at": 1_000},
        suppress_calls=True,
        now_s=2.0,
    )

    assert plan.sync_audio_suspend is True
    assert plan.action == ""


def test_polled_call_plan_does_not_end_fresh_ringing_session_on_immediate_idle():
    plan = plan_polled_call_state(
        "idle",
        previous_state="ringing",
        route_suspended=True,
        call_ui={
            "phase": "ringing",
            "number": "+123",
            "display_name": "Alice",
            "updated_at": 9_200,
        },
        suppress_calls=False,
        now_s=10.0,
    )

    assert plan.sync_audio_restore is True
    assert plan.next_route_suspended is False
    assert plan.action == ""


def test_polled_call_plan_does_not_end_fresh_outbound_dialing_session_on_immediate_idle():
    plan = plan_polled_call_state(
        "idle",
        previous_state="idle",
        route_suspended=False,
        call_ui={
            "phase": "dialing",
            "number": "+123",
            "display_name": "Alice",
            "updated_at": 9_000,
        },
        suppress_calls=False,
        now_s=10.0,
    )

    assert plan.action == ""


def test_polled_call_plan_still_ends_stale_ringing_session_on_idle():
    plan = plan_polled_call_state(
        "idle",
        previous_state="ringing",
        route_suspended=True,
        call_ui={
            "phase": "ringing",
            "number": "+123",
            "display_name": "Alice",
            "updated_at": 1_000,
        },
        suppress_calls=False,
        now_s=10.0,
    )

    assert plan.sync_audio_restore is True
    assert plan.next_route_suspended is False
    assert plan.action == "ended"
