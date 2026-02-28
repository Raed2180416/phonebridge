"""Tailscale status"""
import subprocess, json

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

    def is_connected(self):
        s = self.get_status()
        return bool(s and s.get("BackendState") == "Running")

    def get_self_ip(self):
        s = self.get_status()
        ips = (s or {}).get("Self", {}).get("TailscaleIPs", [])
        return ips[0] if ips else None

    def get_peers(self):
        s = self.get_status()
        if not s:
            return []
        return [{
            "name":      p.get("HostName", "?"),
            "ip":        (p.get("TailscaleIPs") or ["?"])[0],
            "online":    p.get("Online", False),
            "exit_node": p.get("ExitNode", False),
            "os":        p.get("OS", ""),
            "relay":     p.get("Relay", ""),
        } for p in s.get("Peer", {}).values()]

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
