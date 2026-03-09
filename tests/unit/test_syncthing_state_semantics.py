"""Deterministic Syncthing service/API split semantics tests."""

from __future__ import annotations

import types

import backend.syncthing as syncthing


def test_runtime_status_flags_unit_inactive_api_reachable(monkeypatch):
    st = syncthing.Syncthing()
    monkeypatch.setattr(
        st,
        "service_state",
        lambda: {
            "service_active": False,
            "unit_state": "inactive",
            "unit_file_state": "linked-runtime",
            "load_state": "loaded",
            "detail": "linked_runtime",
        },
    )
    monkeypatch.setattr(st, "ping_status", lambda timeout=3: (True, 200, "ok"))

    status = st.get_runtime_status()
    assert status["service_active"] is False
    assert status["api_reachable"] is True
    assert status["mixed_state"] is True
    assert status["reason"] == "unit_inactive_api_reachable"
    assert status["unit_file_state"] == "linked-runtime"


def test_runtime_status_flags_service_active_api_unreachable(monkeypatch):
    st = syncthing.Syncthing()
    monkeypatch.setattr(
        st,
        "service_state",
        lambda: {
            "service_active": True,
            "unit_state": "active",
            "unit_file_state": "enabled",
            "load_state": "loaded",
            "detail": "active",
        },
    )
    monkeypatch.setattr(st, "ping_status", lambda timeout=3: (False, None, "request_failed"))

    status = st.get_runtime_status()
    assert status["service_active"] is True
    assert status["api_reachable"] is False
    assert status["mixed_state"] is True
    assert status["reason"] == "service_active_request_failed"


def test_set_running_false_when_target_not_reached(monkeypatch):
    st = syncthing.Syncthing()

    monkeypatch.setattr(
        syncthing.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    states = iter([False, False, False, False, False, False])
    monkeypatch.setattr(st, "is_service_active", lambda: next(states, False))

    ticks = {"n": 0}

    def _time():
        ticks["n"] += 1
        return ticks["n"] * 0.6

    monkeypatch.setattr(syncthing.time, "time", _time)
    monkeypatch.setattr(syncthing.time, "sleep", lambda _: None)

    assert st.set_running(True) is False


def test_set_running_true_when_target_reached(monkeypatch):
    st = syncthing.Syncthing()

    monkeypatch.setattr(
        syncthing.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    states = iter([False, True])
    monkeypatch.setattr(st, "is_service_active", lambda: next(states, True))

    ticks = {"n": 0}

    def _time():
        ticks["n"] += 1
        return ticks["n"] * 0.4

    monkeypatch.setattr(syncthing.time, "time", _time)
    monkeypatch.setattr(syncthing.time, "sleep", lambda _: None)

    assert st.set_running(True) is True
