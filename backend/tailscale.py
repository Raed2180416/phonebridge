"""Tailscale status"""
import difflib
import json
import re
import subprocess

class Tailscale:
    def __init__(self):
        self._last_error = ""
        self._last_error_kind = ""

    def _run(self, *args):
        try:
            r = subprocess.run(
                ["tailscale", *args],
                capture_output=True,
                text=True,
                timeout=8,
            )
            out = (r.stdout or r.stderr or "").strip()
            if r.returncode != 0:
                self._last_error = out
                self._last_error_kind = self._classify_error(out)
            else:
                self._last_error = ""
                self._last_error_kind = ""
            return r.returncode == 0, out
        except:
            self._last_error = "tailscale command failed"
            self._last_error_kind = "command_failed"
            return False, ""

    @staticmethod
    def _classify_error(message: str) -> str:
        msg = (message or "").lower()
        if (
            "prefs write access denied" in msg
            or "use 'sudo tailscale set --operator=$user'" in msg
            or "operator" in msg and "access denied" in msg
        ):
            return "operator_permission"
        if "failed to connect to local tailscaled" in msg or "tailscaled" in msg and "not running" in msg:
            return "daemon_offline"
        return "unknown"

    def get_status(self):
        try:
            ok, text = self._run("status", "--json")
            if not ok:
                return None
            return json.loads(text)
        except:
            return None

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())

    def _find_phone_peer(self, peers: list[dict], phone_name: str = "", phone_ip: str = "") -> dict | None:
        expected_ip = str(phone_ip or "").strip()
        expected_name = self._norm(phone_name)

        # If no explicit matcher is configured, pick the single non-self peer
        # when unambiguous. This keeps mesh status sane on first-run setups.
        if (not expected_ip) and (not expected_name):
            non_self_online = [p for p in peers if not p.get("self") and p.get("online")]
            if len(non_self_online) == 1:
                return non_self_online[0]

        for peer in peers:
            peer_ips = peer.get("all_ips", []) or []
            if expected_ip and expected_ip in peer_ips:
                return peer
        if expected_name:
            for peer in peers:
                peer_name = self._norm(peer.get("name", ""))
                if peer_name and (expected_name in peer_name or peer_name in expected_name):
                    return peer

            # Fuzzy fallback for cases where device names drift/typo, e.g.
            # nothingphone3apro1 vs nothingphoen3apro1.
            best_peer = None
            best_ratio = 0.0
            for peer in peers:
                peer_name = self._norm(peer.get("name", ""))
                if not peer_name:
                    continue
                ratio = difflib.SequenceMatcher(None, expected_name, peer_name).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_peer = peer
            if best_peer is not None and best_ratio >= 0.70:
                return best_peer

            # If the user configured a phone name but there is only one
            # non-self peer online, treat it as the phone candidate.
            non_self_online = [p for p in peers if not p.get("self") and p.get("online")]
            if len(non_self_online) == 1:
                return non_self_online[0]
        return None

    def get_mesh_snapshot(self, *, phone_name: str = "", phone_ip: str = "") -> dict:
        status = self.get_status() or {}
        backend_state = str(status.get("BackendState") or "").strip()
        self_block = (status.get("Self") or {}) if isinstance(status, dict) else {}
        self_ips = list((self_block.get("TailscaleIPs") or []))
        self_ip = self_ips[0] if self_ips else None
        self_online = bool(self_block.get("Online", False))
        daemon_running = backend_state == "Running"
        local_connected = bool(daemon_running and self_online)

        peers = []
        self_name = str(self_block.get("HostName") or "This device")
        peers.append(
            {
                "name": self_name,
                "ip": self_ip or "?",
                "all_ips": self_ips,
                "online": self_online,
                "exit_node": bool(self_block.get("ExitNode", False)),
                "os": str(self_block.get("OS") or ""),
                "relay": str(self_block.get("Relay") or ""),
                "self": True,
            }
        )
        for p in (status.get("Peer", {}) or {}).values():
            peer_ips = list((p.get("TailscaleIPs") or []))
            peers.append(
                {
                    "name": p.get("HostName", "?"),
                    "ip": peer_ips[0] if peer_ips else "?",
                    "all_ips": peer_ips,
                    "online": bool(p.get("Online", False)),
                    "exit_node": bool(p.get("ExitNode", False)),
                    "os": p.get("OS", ""),
                    "relay": p.get("Relay", ""),
                    "self": False,
                }
            )

        phone_peer = self._find_phone_peer(peers, phone_name=phone_name, phone_ip=phone_ip)
        phone_present = bool(phone_peer is not None)
        phone_online = bool(phone_peer and phone_peer.get("online"))
        mesh_ready = bool(local_connected and phone_online)
        matched_phone_name = str((phone_peer or {}).get("name") or "")
        matched_phone_ip = str((phone_peer or {}).get("ip") or "")

        if not local_connected:
            mesh_reason = f"local={backend_state or 'offline'}"
        elif not phone_present:
            mesh_reason = "phone missing from mesh"
        elif not phone_online:
            mesh_reason = "phone offline in mesh"
        else:
            mesh_reason = "mesh healthy"

        return {
            "backend_state": backend_state,
            "daemon_running": daemon_running,
            "local_connected": local_connected,
            "self_ip": self_ip,
            "self_online": self_online,
            "peers": peers,
            "phone_present": phone_present,
            "phone_online": phone_online,
            "phone_name": matched_phone_name,
            "phone_ip": matched_phone_ip,
            "mesh_ready": mesh_ready,
            "mesh_reason": mesh_reason,
        }

    def is_connected(self):
        return bool(self.get_mesh_snapshot().get("local_connected", False))

    def get_self_ip(self):
        s = self.get_status()
        ips = (s or {}).get("Self", {}).get("TailscaleIPs", [])
        return ips[0] if ips else None

    def get_peers(self):
        snap = self.get_mesh_snapshot()
        rows = []
        for p in snap.get("peers", []):
            row = {
                "name": p.get("name", "?"),
                "ip": p.get("ip", "?"),
                "online": bool(p.get("online", False)),
                "exit_node": bool(p.get("exit_node", False)),
                "os": p.get("os", ""),
                "relay": p.get("relay", ""),
            }
            if p.get("self"):
                row["self"] = True
            rows.append(row)
        return rows

    def set_exit_node(self, name):
        try:
            subprocess.run(["tailscale", "set", f"--exit-node={name}"],
                           check=True, timeout=10)
            return True
        except:
            return False

    def clear_exit_node(self):
        try:
            subprocess.run(["tailscale", "set", "--exit-node="],
                           check=True, timeout=10)
            return True
        except:
            return False

    def up(self):
        ok, _ = self._run("up")
        return ok

    def down(self):
        ok, _ = self._run("down")
        return ok

    def set_enabled(self, enabled: bool):
        return self.up() if bool(enabled) else self.down()

    def last_error(self):
        if self._last_error_kind == "operator_permission":
            return (
                "Tailscale requires operator permission. Run once:\n"
                "sudo tailscale set --operator=$USER"
            )
        return self._last_error

    def last_error_kind(self):
        return self._last_error_kind
