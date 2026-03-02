"""Deterministic tests for KDE watchdog flow."""

from __future__ import annotations

from pathlib import Path

import scripts.kde_watchdog as kw


def _cfg(tmp_path, *, threshold=2, cooldown=600):
    return kw.Config(
        device_id="dev123",
        phone_tailscale_ip="100.127.0.90",
        adb_target="100.127.0.90:5555",
        kde_app_package="org.kde.kdeconnect_tp",
        fail_threshold=threshold,
        wake_cooldown_sec=cooldown,
        state_dir=Path(tmp_path) / "state",
    )


def test_kde_healthy_no_recovery(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)
    calls = {"refresh": 0, "wake": 0}

    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: True)
    monkeypatch.setattr(kw, "kde_refresh", lambda: calls.__setitem__("refresh", calls["refresh"] + 1) or True)
    monkeypatch.setattr(kw, "wake_phone_kde_app", lambda target, package: calls.__setitem__("wake", calls["wake"] + 1) or True)

    code = kw.run_watchdog(cfg, sleep_fn=lambda _: None)
    assert code == 0
    assert calls["refresh"] == 0
    assert calls["wake"] == 0
    assert kw.read_int(cfg.state_dir / "fail_count", 0) == 0


def test_one_failed_check_only_increments_counter(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path, threshold=2)
    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: False)

    code = kw.run_watchdog(cfg, sleep_fn=lambda _: None)
    assert code == 0
    assert kw.read_int(cfg.state_dir / "fail_count", 0) == 1


def test_threshold_hit_refresh_recovers_no_adb_wake(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path, threshold=2)
    kw.write_int(cfg.state_dir / "fail_count", 1)

    seq = iter([False, True])
    calls = {"refresh": 0, "wake": 0}

    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: next(seq))
    monkeypatch.setattr(kw, "kde_refresh", lambda: calls.__setitem__("refresh", calls["refresh"] + 1) or True)
    monkeypatch.setattr(kw, "wake_phone_kde_app", lambda target, package: calls.__setitem__("wake", calls["wake"] + 1) or True)

    code = kw.run_watchdog(cfg, sleep_fn=lambda _: None)
    assert code == 0
    assert calls["refresh"] == 1
    assert calls["wake"] == 0
    assert kw.read_int(cfg.state_dir / "fail_count", 99) == 0


def test_threshold_hit_refresh_fails_and_gates_false(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path, threshold=2)
    kw.write_int(cfg.state_dir / "fail_count", 1)

    seq = iter([False, False])
    calls = {"wake": 0}

    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: next(seq))
    monkeypatch.setattr(kw, "kde_refresh", lambda: True)
    monkeypatch.setattr(kw, "tailscale_status", lambda: None)
    monkeypatch.setattr(kw, "ensure_adb_connected", lambda target: True)
    monkeypatch.setattr(kw, "wake_phone_kde_app", lambda target, package: calls.__setitem__("wake", calls["wake"] + 1) or True)

    code = kw.run_watchdog(cfg, sleep_fn=lambda _: None)
    assert code == 0
    assert calls["wake"] == 0


def test_threshold_hit_and_gates_true_wakes_and_sets_cooldown(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path, threshold=2, cooldown=600)
    kw.write_int(cfg.state_dir / "fail_count", 1)

    seq = iter([False, False, True])
    calls = {"wake": 0, "refresh": 0}

    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: next(seq))
    monkeypatch.setattr(kw, "kde_refresh", lambda: calls.__setitem__("refresh", calls["refresh"] + 1) or True)
    monkeypatch.setattr(kw, "tailscale_status", lambda: {"BackendState": "Running", "Self": {"Online": True}, "Peer": {}})
    monkeypatch.setattr(kw, "tailscale_local_online", lambda status: True)
    monkeypatch.setattr(kw, "tailscale_phone_online", lambda status, phone_ip: True)
    monkeypatch.setattr(kw, "ensure_adb_connected", lambda target: True)
    monkeypatch.setattr(kw, "wake_phone_kde_app", lambda target, package: calls.__setitem__("wake", calls["wake"] + 1) or True)

    code = kw.run_watchdog(cfg, time_fn=lambda: 1000, sleep_fn=lambda _: None)
    assert code == 0
    assert calls["wake"] == 1
    assert calls["refresh"] == 2
    assert kw.read_int(cfg.state_dir / "last_wake_epoch", 0) == 1000
    assert kw.read_int(cfg.state_dir / "fail_count", 99) == 0


def test_cooldown_active_skips_repeated_wake(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path, threshold=2, cooldown=100)
    kw.write_int(cfg.state_dir / "fail_count", 1)
    kw.write_int(cfg.state_dir / "last_wake_epoch", 950)

    seq = iter([False, False])
    calls = {"wake": 0}

    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: next(seq))
    monkeypatch.setattr(kw, "kde_refresh", lambda: True)
    monkeypatch.setattr(kw, "tailscale_status", lambda: {"BackendState": "Running", "Self": {"Online": True}, "Peer": {}})
    monkeypatch.setattr(kw, "tailscale_local_online", lambda status: True)
    monkeypatch.setattr(kw, "tailscale_phone_online", lambda status, phone_ip: True)
    monkeypatch.setattr(kw, "ensure_adb_connected", lambda target: True)
    monkeypatch.setattr(kw, "wake_phone_kde_app", lambda target, package: calls.__setitem__("wake", calls["wake"] + 1) or True)

    code = kw.run_watchdog(cfg, time_fn=lambda: 1000, sleep_fn=lambda _: None)
    assert code == 0
    assert calls["wake"] == 0


def test_tailscale_status_failure_gracefully_skips_wake(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path, threshold=2)
    kw.write_int(cfg.state_dir / "fail_count", 1)

    seq = iter([False, False])
    calls = {"wake": 0}

    monkeypatch.setattr(kw, "is_kde_reachable", lambda device_id: next(seq))
    monkeypatch.setattr(kw, "kde_refresh", lambda: True)
    monkeypatch.setattr(kw, "tailscale_status", lambda: None)
    monkeypatch.setattr(kw, "ensure_adb_connected", lambda target: True)
    monkeypatch.setattr(kw, "wake_phone_kde_app", lambda target, package: calls.__setitem__("wake", calls["wake"] + 1) or True)

    code = kw.run_watchdog(cfg, sleep_fn=lambda _: None)
    assert code == 0
    assert calls["wake"] == 0


def test_adb_connect_not_counted_connected_without_device_state(monkeypatch):
    def fake_run(cmd, timeout=8.0):
        if cmd[:2] == ["adb", "connect"]:
            return kw.CommandResult(True, "connected to 100.127.0.90:5555\n", "", 0)
        if cmd == ["adb", "devices"]:
            return kw.CommandResult(True, "List of devices attached\n100.127.0.90:5555\toffline\n", "", 0)
        raise AssertionError(f"unexpected cmd {cmd}")

    monkeypatch.setattr(kw, "run_cmd", fake_run)
    assert kw.ensure_adb_connected("100.127.0.90:5555") is False
