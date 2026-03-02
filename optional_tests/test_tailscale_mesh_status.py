"""Tailscale mesh status semantics."""

from backend.tailscale import Tailscale


def _mk_status(*, backend="Running", self_online=True, self_ip="100.71.39.20", peers=None):
    return {
        "BackendState": backend,
        "Self": {
            "HostName": "laptop",
            "Online": self_online,
            "TailscaleIPs": [self_ip] if self_ip else [],
        },
        "Peer": {str(i): p for i, p in enumerate(peers or [])},
    }


def test_is_connected_requires_self_online(monkeypatch):
    ts = Tailscale()
    monkeypatch.setattr(
        ts,
        "get_status",
        lambda: _mk_status(backend="Running", self_online=False),
    )
    assert ts.is_connected() is False


def test_mesh_snapshot_detects_phone_by_ip(monkeypatch):
    ts = Tailscale()
    peer = {
        "HostName": "NothingPhone",
        "Online": True,
        "TailscaleIPs": ["100.127.0.90"],
    }
    monkeypatch.setattr(ts, "get_status", lambda: _mk_status(peers=[peer]))
    snap = ts.get_mesh_snapshot(phone_name="Nothing Phone 3a Pro", phone_ip="100.127.0.90")
    assert snap["local_connected"] is True
    assert snap["phone_present"] is True
    assert snap["phone_online"] is True
    assert snap["mesh_ready"] is True


def test_mesh_snapshot_flags_phone_missing(monkeypatch):
    ts = Tailscale()
    monkeypatch.setattr(ts, "get_status", lambda: _mk_status(peers=[]))
    snap = ts.get_mesh_snapshot(phone_name="Nothing Phone 3a Pro", phone_ip="100.127.0.90")
    assert snap["local_connected"] is True
    assert snap["phone_present"] is False
    assert snap["mesh_ready"] is False
