"""Tailscale status"""
import subprocess, json

class Tailscale:
    def _run(self, *args):
        try:
            r = subprocess.run(
                ["tailscale", *args],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return r.returncode == 0, (r.stdout or r.stderr or "").strip()
        except:
            return False, ""

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
