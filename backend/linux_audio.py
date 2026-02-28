"""Linux audio helpers for Phase 1 call-audio routing (Bluetooth HFP/HSP)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time


class LinuxAudio:
    def __init__(self):
        self._has_pactl = bool(shutil.which("pactl"))
        self._has_wpctl = bool(shutil.which("wpctl"))
        self._has_pw_dump = bool(shutil.which("pw-dump"))

    def available(self) -> bool:
        return self._has_pactl or (self._has_wpctl and self._has_pw_dump)

    def _run(self, *args, timeout=4):
        try:
            r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
            return r.returncode == 0, (r.stdout or r.stderr or "").strip()
        except Exception:
            return False, ""

    # ── Defaults ───────────────────────────────────────────────
    def default_sink(self) -> str:
        if self._has_pactl:
            ok, out = self._run("pactl", "get-default-sink", timeout=2)
            return out if ok else ""
        return self._wp_default_id(section="Sinks")

    def default_source(self) -> str:
        if self._has_pactl:
            ok, out = self._run("pactl", "get-default-source", timeout=2)
            return out if ok else ""
        return self._wp_default_id(section="Sources")

    def set_default_sink(self, sink_name_or_id: str) -> bool:
        if not sink_name_or_id:
            return False
        if self._has_pactl:
            return self._run("pactl", "set-default-sink", sink_name_or_id, timeout=3)[0]
        target = self._wp_resolve_node_id("Audio/Sink", str(sink_name_or_id))
        if not target:
            return False
        return self._run("wpctl", "set-default", target, timeout=3)[0]

    def set_default_source(self, source_name_or_id: str) -> bool:
        if not source_name_or_id:
            return False
        if self._has_pactl:
            return self._run("pactl", "set-default-source", source_name_or_id, timeout=3)[0]
        target = self._wp_resolve_node_id("Audio/Source", str(source_name_or_id))
        if not target:
            return False
        return self._run("wpctl", "set-default", target, timeout=3)[0]

    # ── Device lists + levels ───────────────────────────────────
    def list_sinks(self):
        if self._has_pactl:
            return self._list_pactl_nodes("sinks", include_monitors=False, default_name=self.default_sink())
        return self._list_pw_nodes("Audio/Sink", default_id=self._wp_default_id(section="Sinks"))

    def list_sources(self):
        if self._has_pactl:
            return self._list_pactl_nodes("sources", include_monitors=False, default_name=self.default_source())
        return self._list_pw_nodes("Audio/Source", default_id=self._wp_default_id(section="Sources"))

    def set_sink_volume(self, sink_name_or_id: str, percent: int) -> bool:
        pct = max(0, min(200, int(percent)))
        target = str(sink_name_or_id or "@DEFAULT_SINK@")
        if self._has_pactl:
            return self._run("pactl", "set-sink-volume", target, f"{pct}%", timeout=3)[0]
        wp_target = self._wp_resolve_node_id("Audio/Sink", str(sink_name_or_id)) or "@DEFAULT_AUDIO_SINK@"
        return self._run("wpctl", "set-volume", wp_target, f"{pct/100.0:.2f}", timeout=3)[0]

    def set_source_volume(self, source_name_or_id: str, percent: int) -> bool:
        pct = max(0, min(200, int(percent)))
        target = str(source_name_or_id or "@DEFAULT_SOURCE@")
        if self._has_pactl:
            return self._run("pactl", "set-source-volume", target, f"{pct}%", timeout=3)[0]
        wp_target = self._wp_resolve_node_id("Audio/Source", str(source_name_or_id)) or "@DEFAULT_AUDIO_SOURCE@"
        return self._run("wpctl", "set-volume", wp_target, f"{pct/100.0:.2f}", timeout=3)[0]

    def get_sink_volume(self, sink_name_or_id: str = "") -> int | None:
        target = str(sink_name_or_id or "@DEFAULT_SINK@")
        if self._has_pactl:
            ok, out = self._run("pactl", "get-sink-volume", target, timeout=3)
            if ok:
                m = re.search(r"\b(\d+)%", out or "")
                if m:
                    return int(m.group(1))
        wp_target = self._wp_resolve_node_id("Audio/Sink", str(sink_name_or_id)) or "@DEFAULT_AUDIO_SINK@"
        ok, out = self._run("wpctl", "get-volume", wp_target, timeout=3)
        if ok:
            m = re.search(r"([0-9]*\.?[0-9]+)", out or "")
            if m:
                return int(float(m.group(1)) * 100)
        return None

    def get_source_volume(self, source_name_or_id: str = "") -> int | None:
        target = str(source_name_or_id or "@DEFAULT_SOURCE@")
        if self._has_pactl:
            ok, out = self._run("pactl", "get-source-volume", target, timeout=3)
            if ok:
                m = re.search(r"\b(\d+)%", out or "")
                if m:
                    return int(m.group(1))
        wp_target = self._wp_resolve_node_id("Audio/Source", str(source_name_or_id)) or "@DEFAULT_AUDIO_SOURCE@"
        ok, out = self._run("wpctl", "get-volume", wp_target, timeout=3)
        if ok:
            m = re.search(r"([0-9]*\.?[0-9]+)", out or "")
            if m:
                return int(float(m.group(1)) * 100)
        return None

    def set_source_mute(self, muted: bool, source_name_or_id: str = "") -> bool:
        target = str(source_name_or_id or "@DEFAULT_SOURCE@")
        if self._has_pactl:
            return self._run("pactl", "set-source-mute", target, "1" if muted else "0", timeout=3)[0]
        wp_target = self._wp_resolve_node_id("Audio/Source", str(source_name_or_id)) or "@DEFAULT_AUDIO_SOURCE@"
        return self._run("wpctl", "set-mute", wp_target, "1" if muted else "0", timeout=3)[0]

    def set_sink_mute(self, muted: bool, sink_name_or_id: str = "") -> bool:
        target = str(sink_name_or_id or "@DEFAULT_SINK@")
        if self._has_pactl:
            return self._run("pactl", "set-sink-mute", target, "1" if muted else "0", timeout=3)[0]
        wp_target = self._wp_resolve_node_id("Audio/Sink", str(sink_name_or_id)) or "@DEFAULT_AUDIO_SINK@"
        return self._run("wpctl", "set-mute", wp_target, "1" if muted else "0", timeout=3)[0]

    # ── Bluetooth card/device discovery ────────────────────────
    def list_bt_cards(self):
        if self._has_pactl:
            return self._list_bt_cards_pactl()
        return self._list_bt_cards_pipewire()

    def _list_bt_cards_pactl(self):
        ok, short = self._run("pactl", "list", "short", "cards", timeout=3)
        if not ok:
            return []
        names = []
        for line in short.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].startswith("bluez_card."):
                names.append(parts[1].strip())

        ok, full = self._run("pactl", "list", "cards", timeout=4)
        if not ok:
            return [{"name": n, "description": n, "profiles": [], "active_profile": ""} for n in names]

        cards = []
        for name in names:
            block = self._card_block(full, name)
            cards.append({
                "name": name,
                "description": self._parse_card_description(block) or name,
                "profiles": self._parse_profiles(block),
                "active_profile": self._parse_active_profile(block),
                "profile_map": {},
            })
        return cards

    def _list_bt_cards_pipewire(self):
        data = self._pw_dump()
        if not data:
            return []
        cards = []
        for obj in data:
            if "Device" not in str(obj.get("type", "")):
                continue
            props = (((obj.get("info") or {}).get("props")) or {})
            if str(props.get("device.api", "")) != "bluez5":
                continue
            dev_id = int(obj.get("id", -1))
            if dev_id < 0:
                continue

            params = ((obj.get("info") or {}).get("params") or {})
            enum_profiles = params.get("EnumProfile", []) or []
            profile_map = {}
            for row in enum_profiles:
                name = str(row.get("name", "")).strip()
                idx = row.get("index")
                if name != "" and isinstance(idx, int):
                    profile_map[name] = idx

            active_profile = ""
            active_rows = params.get("Profile", []) or []
            if active_rows:
                active_idx = active_rows[0].get("index")
                for name, idx in profile_map.items():
                    if idx == active_idx:
                        active_profile = name
                        break

            cards.append({
                "name": f"id:{dev_id}",
                "description": str(props.get("device.description") or props.get("device.nick") or f"Bluetooth Device {dev_id}"),
                "profiles": list(profile_map.keys()),
                "active_profile": active_profile,
                "profile_map": profile_map,
                "wp_id": dev_id,
            })
        return cards

    # ── Profile + routing takeover ─────────────────────────────
    @staticmethod
    def _profile_priority():
        return [
            "audio-gateway",
            "audio_gateway",
            "handsfree_head_unit",
            "headset_head_unit",
            "hfp_hf",
            "hsp_hs",
            "handsfree_audio_gateway",
            "headset_audio_gateway",
            "hfp_ag",
            "hsp_ag",
            "headset",
            "handsfree",
        ]

    def choose_hfp_profile(self, card):
        profiles = list(card.get("profiles", []) or [])
        usable = [p for p in profiles if str(p).strip().lower() not in {"off", "none", "disabled"}]
        if usable:
            profiles = usable
        if not profiles:
            return "", None
        for preferred in self._profile_priority():
            for prof in profiles:
                if prof == preferred or preferred in prof:
                    idx = (card.get("profile_map") or {}).get(prof)
                    return prof, idx
        prof = profiles[0]
        idx = (card.get("profile_map") or {}).get(prof)
        return prof, idx

    def activate_hfp_for_card(self, card_name: str):
        cards = self.list_bt_cards()
        card = next((c for c in cards if c.get("name") == card_name), None)
        if not card:
            return False, "Bluetooth card not found"

        profile_name, profile_idx = self.choose_hfp_profile(card)
        if not profile_name:
            return False, "No profile available on selected Bluetooth card"

        # Set BT profile to headset/handsfree where possible.
        if str(card_name).startswith("id:"):
            dev_id = int(str(card_name).split(":", 1)[1])
            if profile_idx is None:
                return False, f"Profile index unavailable for {profile_name}"
            ok, _ = self._run("wpctl", "set-profile", str(dev_id), str(profile_idx), timeout=4)
            if not ok:
                return False, f"Failed to set profile index {profile_idx}"
            time.sleep(0.4)
            sink_id, source_id = self._wp_bt_node_ids(dev_id)
            if sink_id:
                self.set_default_sink(str(sink_id))
            if source_id:
                self.set_default_source(str(source_id))
            if not sink_id and not source_id:
                return False, f"Profile set ({profile_name}) but no BT sink/source node found"
            return True, f"HFP active ({profile_name}) | sink={sink_id or '-'} | mic={source_id or '-'}"

        if not self.set_card_profile(card_name, profile_name):
            return False, f"Failed to set profile: {profile_name}"
        time.sleep(0.45)
        sink_name, source_name = self._pactl_bt_io_names(card_name)
        if sink_name:
            self.set_default_sink(sink_name)
        if source_name:
            self.set_default_source(source_name)
        if not sink_name and not source_name:
            return False, f"Profile set ({profile_name}) but no BT sink/source appeared"
        return True, f"HFP active ({profile_name}) | sink={sink_name or '-'} | mic={source_name or '-'}"

    def restore_defaults(self, sink_name_or_id: str, source_name_or_id: str) -> bool:
        ok_sink = self.set_default_sink(sink_name_or_id) if sink_name_or_id else False
        ok_src = self.set_default_source(source_name_or_id) if source_name_or_id else False
        return bool(ok_sink or ok_src)

    def diagnostics(self):
        return {
            "default_sink": self.default_sink(),
            "default_source": self.default_source(),
            "cards": self.list_bt_cards(),
            "engine": "pactl" if self._has_pactl else ("wpctl" if self._has_wpctl else "none"),
        }

    # ── pactl helpers ──────────────────────────────────────────
    def set_card_profile(self, card_name: str, profile_name: str) -> bool:
        if not self._has_pactl:
            return False
        return self._run("pactl", "set-card-profile", card_name, profile_name, timeout=4)[0]

    def _pactl_bt_io_names(self, card_name: str):
        addr = card_name.replace("bluez_card.", "")
        sink_prefix = f"bluez_output.{addr}"
        source_prefix = f"bluez_input.{addr}"
        sink_name = ""
        source_name = ""

        ok, sinks_out = self._run("pactl", "list", "short", "sinks", timeout=3)
        if ok:
            sink_rows = [ln.split("\t")[1].strip() for ln in sinks_out.splitlines() if "\t" in ln]
            for s in sink_rows:
                if s.startswith(sink_prefix) and ("handsfree" in s or "headset" in s):
                    sink_name = s
                    break
            if not sink_name:
                for s in sink_rows:
                    if s.startswith(sink_prefix):
                        sink_name = s
                        break

        ok, src_out = self._run("pactl", "list", "short", "sources", timeout=3)
        if ok:
            src_rows = [ln.split("\t")[1].strip() for ln in src_out.splitlines() if "\t" in ln]
            for s in src_rows:
                if s.startswith(source_prefix) and ("handsfree" in s or "headset" in s):
                    source_name = s
                    break
            if not source_name:
                for s in src_rows:
                    if s.startswith(source_prefix):
                        source_name = s
                        break
        return sink_name, source_name

    @staticmethod
    def _card_block(full_text: str, card_name: str) -> str:
        marker = f"Name: {card_name}"
        pos = full_text.find(marker)
        if pos < 0:
            return ""
        start = full_text.rfind("Card #", 0, pos)
        if start < 0:
            start = pos
        end = full_text.find("Card #", pos + 1)
        if end < 0:
            end = len(full_text)
        return full_text[start:end]

    @staticmethod
    def _parse_card_description(block: str) -> str:
        m = re.search(r'device\.description = "([^"]+)"', block or "")
        return m.group(1).strip() if m else ""

    @staticmethod
    def _parse_profiles(block: str):
        out = []
        in_profiles = False
        for raw in (block or "").splitlines():
            line = raw.rstrip()
            if line.strip().startswith("Profiles:"):
                in_profiles = True
                continue
            if in_profiles and line.strip().startswith("Active Profile:"):
                break
            if not in_profiles:
                continue
            m = re.match(r"\s+([a-zA-Z0-9_:\-\.]+):\s", line)
            if m:
                out.append(m.group(1).strip())
        return out

    @staticmethod
    def _parse_active_profile(block: str) -> str:
        m = re.search(r"Active Profile:\s*([a-zA-Z0-9_:\-\.]+)", block or "")
        return m.group(1).strip() if m else ""

    # ── wpctl/pw-dump helpers ──────────────────────────────────
    def _pw_dump(self):
        if not self._has_pw_dump:
            return []
        ok, out = self._run("pw-dump", timeout=5)
        if not ok or not out:
            return []
        try:
            return json.loads(out)
        except Exception:
            return []

    def _wp_default_id(self, section="Sinks"):
        if not self._has_wpctl:
            return ""
        ok, out = self._run("wpctl", "status", timeout=3)
        if not ok:
            return ""
        in_audio = False
        in_section = False
        for raw in out.splitlines():
            line = raw.rstrip()
            if line.startswith("Audio"):
                in_audio = True
                in_section = False
                continue
            if in_audio and line.startswith("Video"):
                break
            if not in_audio:
                continue
            stripped = line.strip()
            if stripped.startswith(f"├─ {section}:") or stripped.startswith(f"└─ {section}:"):
                in_section = True
                continue
            if in_section and stripped.startswith("├─"):
                break
            if in_section:
                m = re.search(r"\*\s+(\d+)\.\s", line)
                if m:
                    return m.group(1)
        return ""

    def _wp_bt_node_ids(self, device_id: int):
        data = self._pw_dump()
        if not data:
            return "", ""
        sinks = []
        sources = []
        for obj in data:
            if "Node" not in str(obj.get("type", "")):
                continue
            props = (((obj.get("info") or {}).get("props")) or {})
            if int(props.get("device.id", -1)) != int(device_id):
                continue
            media_class = str(props.get("media.class", ""))
            name = str(props.get("node.name", "")).lower()
            desc = str(props.get("node.description", "")).lower()
            row = {
                "id": str(obj.get("id")),
                "name": name,
                "desc": desc,
            }
            if media_class == "Audio/Sink":
                sinks.append(row)
            elif media_class == "Audio/Source":
                sources.append(row)

        def pick(rows):
            for r in rows:
                if any(k in r["name"] or k in r["desc"] for k in ("handsfree", "headset", "hfp", "hsp")):
                    return r["id"]
            return rows[0]["id"] if rows else ""

        return pick(sinks), pick(sources)

    def _list_pactl_nodes(self, section: str, *, include_monitors: bool, default_name: str):
        ok, out = self._run("pactl", "list", "short", section, timeout=3)
        if not ok:
            return []
        desc_map = self._pactl_description_map(section)
        rows = []
        for raw in (out or "").splitlines():
            parts = raw.split("\t")
            if len(parts) < 2:
                continue
            node_id = str(parts[0]).strip()
            name = str(parts[1]).strip()
            if (not include_monitors) and section == "sources" and name.endswith(".monitor"):
                continue
            rows.append(
                {
                    "id": node_id,
                    "name": name,
                    "description": desc_map.get(name, name),
                    "selector": name,
                    "is_default": name == str(default_name or "").strip(),
                }
            )
        return rows

    def _pactl_description_map(self, section: str):
        # `section` must be `sinks` or `sources`
        singular = section[:-1] if section.endswith("s") else section
        ok, out = self._run("pactl", "list", section, timeout=4)
        if not ok:
            return {}
        mapping = {}
        current_name = ""
        current_desc = ""
        for raw in (out or "").splitlines():
            line = raw.rstrip()
            if line.startswith(f"{singular.capitalize()} #"):
                if current_name:
                    mapping[current_name] = current_desc or current_name
                current_name = ""
                current_desc = ""
                continue
            stripped = line.strip()
            if stripped.startswith("Name:"):
                current_name = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith("Description:"):
                current_desc = stripped.split(":", 1)[1].strip()
                continue
        if current_name:
            mapping[current_name] = current_desc or current_name
        return mapping

    def _list_pw_nodes(self, media_class: str, *, default_id: str):
        data = self._pw_dump()
        if not data:
            return []
        rows = []
        for obj in data:
            if "Node" not in str(obj.get("type", "")):
                continue
            props = (((obj.get("info") or {}).get("props")) or {})
            if str(props.get("media.class", "")) != media_class:
                continue
            node_id = str(obj.get("id", ""))
            if not node_id:
                continue
            name = str(props.get("node.name", "")).strip()
            desc = str(props.get("node.description", "") or name).strip()
            rows.append(
                {
                    "id": node_id,
                    "name": name or node_id,
                    "description": desc or node_id,
                    "selector": node_id,
                    "is_default": node_id == str(default_id or ""),
                }
            )
        return rows

    def _wp_resolve_node_id(self, media_class: str, token: str) -> str:
        text = str(token or "").strip()
        if not text:
            return ""
        if text.isdigit():
            return text
        data = self._pw_dump()
        if not data:
            return ""
        for obj in data:
            if "Node" not in str(obj.get("type", "")):
                continue
            props = (((obj.get("info") or {}).get("props")) or {})
            if str(props.get("media.class", "")) != media_class:
                continue
            node_id = str(obj.get("id", "")).strip()
            if not node_id:
                continue
            node_name = str(props.get("node.name", "")).strip()
            node_desc = str(props.get("node.description", "")).strip()
            if text == node_name or text == node_desc:
                return node_id
        return ""
