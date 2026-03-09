"""KDE Connect D-Bus interface — all plugins"""
import difflib
import logging
import os
import subprocess

import dbus
import dbus.mainloop.glib
from gi.repository import GLib
from backend.settings_store import get as setting, set as set_setting
from backend import kde_notifications
from backend import kde_signals
from backend import runtime_config

BUS_NAME  = "org.kde.kdeconnect"
log = logging.getLogger(__name__)

def get_bus():
    return dbus.SessionBus()

class KDEConnect:
    def __init__(self):
        self.device_id = runtime_config.device_id()
        self._bus = None
        self._signal_receivers: list[tuple[object, object, dict]] = []
        self.log = log
        self._refresh_device_binding()

    @property
    def bus(self):
        if self._bus is None:
            self._bus = dbus.SessionBus()
        return self._bus

    def _obj(self, path):
        return self.bus.get_object(BUS_NAME, path, introspect=False)

    def _iface(self, path, iface):
        return dbus.Interface(self._obj(path), iface)

    def _add_signal_receiver(self, callback, **kwargs):
        match = self.bus.add_signal_receiver(callback, **kwargs)
        self._signal_receivers.append((match, callback, dict(kwargs)))
        return match

    def disconnect_all_signals(self):
        bus = self._bus
        while self._signal_receivers:
            match, callback, kwargs = self._signal_receivers.pop()
            try:
                if hasattr(match, "remove"):
                    match.remove()
                elif bus is not None and hasattr(bus, "remove_signal_receiver"):
                    bus.remove_signal_receiver(callback, **kwargs)
            except Exception:
                log.debug("Failed removing KDE signal receiver kwargs=%s", kwargs, exc_info=True)

    def _refresh_device_binding(self):
        """Ensure self.device_id points to a currently valid KDE device.

        Device IDs can drift after phone resets/re-pairs. If the configured
        ID is stale, discover candidates from kdeconnectd and pick the best
        match (prefer reachable+paired, then name similarity).
        """
        current = str(self.device_id or "").strip()
        try:
            daemon_obj = self.bus.get_object(BUS_NAME, "/modules/kdeconnect", introspect=False)
            daemon = dbus.Interface(daemon_obj, "org.kde.kdeconnect.daemon")
            reachable = [str(x) for x in (daemon.devices(True, True) or [])]
            paired = [str(x) for x in (daemon.devices(False, True) or [])]
            candidates = []
            for did in reachable + paired:
                if did not in candidates:
                    candidates.append(did)
            if not candidates:
                return

            # If current binding still exists, keep it.
            if current and current in candidates:
                return

            desired_name = str(setting("device_name", "") or "").strip()
            desired_norm = self._norm(desired_name)

            chosen = candidates[0]
            best_score = -1.0
            for did in candidates:
                name = self._device_name_for_id(did)
                name_norm = self._norm(name)
                score = 0.0
                if did in reachable:
                    score += 2.0
                if desired_norm and name_norm:
                    if desired_norm in name_norm or name_norm in desired_norm:
                        score += 2.0
                    score += difflib.SequenceMatcher(None, desired_norm, name_norm).ratio()
                if score > best_score:
                    best_score = score
                    chosen = did

            if chosen and chosen != current:
                self.device_id = chosen
                try:
                    set_setting("device_id", chosen)
                except Exception:
                    pass
                log.info("KDE device binding updated old=%s new=%s", current or "<unset>", chosen)
        except Exception as exc:
            log.debug("KDE device binding refresh skipped: %s", exc)

    @staticmethod
    def _norm(s: str) -> str:
        return "".join(ch for ch in str(s or "").lower() if ch.isalnum())

    def _device_name_for_id(self, device_id: str) -> str:
        did = str(device_id or "").strip()
        if not did:
            return ""
        try:
            path = f"/modules/kdeconnect/devices/{did}"
            props = dbus.Interface(self._obj(path), "org.freedesktop.DBus.Properties")
            return str(props.Get("org.kde.kdeconnect.device", "name") or "")
        except Exception:
            return ""

    def _dev(self, plugin=None):
        p = f"/modules/kdeconnect/devices/{self.device_id}"
        return p if not plugin else f"{p}/{plugin}"

    def _prop(self, plugin, interface, prop):
        try:
            iface = dbus.Interface(self._obj(self._dev(plugin)),
                                   "org.freedesktop.DBus.Properties")
            return iface.Get(interface, prop, timeout=2)
        except Exception as e:
            log.warning("Property read failed %s/%s: %s", plugin, prop, e)
            return None

    # ── Battery ────────────────────────────────────────────────
    # Properties: readonly i charge, readonly b isCharging
    # Signal:     refreshed(b isCharging, i charge)
    def get_battery(self):
        try:
            charge   = self._prop("battery", "org.kde.kdeconnect.device.battery", "charge")
            charging = self._prop("battery", "org.kde.kdeconnect.device.battery", "isCharging")
            return {"charge": int(charge) if charge is not None else -1,
                    "is_charging": bool(charging)}
        except Exception as e:
            log.warning("Battery read failed: %s", e)
            return {"charge": -1, "is_charging": False}

    # ── Connectivity ───────────────────────────────────────────
    # Properties: readonly s cellularNetworkType, readonly i cellularNetworkStrength
    # Signal:     refreshed(s cellularNetworkType, i cellularNetworkStrength)
    def get_network_type(self):
        try:
            t = self._prop("connectivity_report",
                           "org.kde.kdeconnect.device.connectivity_report",
                           "cellularNetworkType")
            return str(t) if t else "Unknown"
        except Exception:
            log.debug("Network type read failed", exc_info=True)
            return "Unknown"

    def get_signal_strength(self):
        try:
            s = self._prop("connectivity_report",
                           "org.kde.kdeconnect.device.connectivity_report",
                           "cellularNetworkStrength")
            return int(s) if s is not None else -1
        except Exception:
            log.debug("Signal strength read failed", exc_info=True)
            return -1

    # ── Find My Phone ──────────────────────────────────────────
    # Method: ring()
    def ring(self):
        try:
            self._iface(self._dev("findmyphone"),
                        "org.kde.kdeconnect.device.findmyphone").ring()
            return True
        except Exception as e:
            log.warning("Ring failed: %s", e)
            return False

    # ── Notifications ──────────────────────────────────────────
    # Methods: activeNotifications() → as
    #          sendReply(s replyId, s message)
    #          sendAction(s key, s action)
    # Signals: notificationPosted(s publicId)
    #          notificationRemoved(s publicId)
    #          notificationUpdated(s publicId)
    #          allNotificationsRemoved()
    def get_notifications(self):
        return kde_notifications.get_notifications(self)

    def dismiss_notification(self, notif_id):
        return kde_notifications.dismiss_notification(self, notif_id)

    def open_notification_reply(self, notif_id):
        return kde_notifications.open_notification_reply(self, notif_id)

    def reply_notification(self, reply_id, message):
        return kde_notifications.reply_notification(self, reply_id, message)

    def send_notification_action(self, key, action):
        return kde_notifications.send_notification_action(self, key, action)

    # ── SMS ────────────────────────────────────────────────────
    # Methods: sendSms(av addresses, s textMessage, av attachmentUrls)
    #          sendSms(av addresses, s textMessage, av attachmentUrls, x subID)
    #          requestAllConversations()
    #          requestConversation(x conversationID [, x rangeStartTimestamp [, x numberToRequest]])
    #          launchApp()
    #          requestAttachment(x partID, s uniqueIdentifier)
    #          getAttachment(x partID, s uniqueIdentifier)
    def send_sms(self, number, message):
        iface = self._iface(self._dev("sms"), "org.kde.kdeconnect.device.sms")
        stripped = number.strip()
        try:
            addr = dbus.Array([dbus.String(stripped, variant_level=1)], signature="v")
            attachments = dbus.Array([], signature="v")
            iface.sendSms(addr, dbus.String(message), attachments)
            return True
        except Exception:
            pass

        try:
            iface.sendSms([stripped], message, [])
            return True
        except Exception:
            pass

        try:
            iface.sendSms([stripped], message, [], dbus.Int64(-1))
            return True
        except Exception as e:
            log.warning("SMS send failed to %s: %s", stripped, e)
            return False

    def request_conversations(self):
        try:
            self._iface(self._dev("sms"),
                        "org.kde.kdeconnect.device.sms").requestAllConversations()
            return True
        except Exception as e:
            log.warning("Conversation refresh failed: %s", e)
            return False

    def request_conversation(self, conversation_id):
        try:
            self._iface(self._dev("sms"),
                        "org.kde.kdeconnect.device.sms"
                        ).requestConversation(dbus.Int64(conversation_id))
            return True
        except Exception as e:
            log.warning("Conversation request failed %s: %s", conversation_id, e)
            return False

    def launch_sms_app(self):
        try:
            self._iface(self._dev("sms"),
                        "org.kde.kdeconnect.device.sms").launchApp()
            return True
        except Exception as e:
            log.warning("Launch SMS app failed: %s", e)
            return False

    # ── Share / File Transfer ──────────────────────────────────
    # Methods: shareUrl(s url)
    #          shareUrls(as urls)
    #          shareText(s text)
    #          openFile(s file)
    # Signal:  shareReceived(s url)
    def share_file(self, filepath):
        """Send a file from NixOS to phone via shareUrl"""
        try:
            import os
            uri = f"file://{os.path.abspath(filepath)}"
            self._iface(self._dev("share"),
                        "org.kde.kdeconnect.device.share").shareUrl(uri)
            return True
        except Exception as e:
            log.warning("Share file failed %s: %s", filepath, e)
            return False

    def share_files(self, filepaths):
        """Send multiple files at once via shareUrls(as urls)"""
        try:
            import os
            uris = [f"file://{os.path.abspath(p)}" for p in filepaths]
            self._iface(self._dev("share"),
                        "org.kde.kdeconnect.device.share").shareUrls(uris)
            return True
        except Exception as e:
            log.warning("Share multiple files failed: %s", e)
            return False

    def share_text(self, text):
        """shareText(s text)"""
        try:
            self._iface(self._dev("share"),
                        "org.kde.kdeconnect.device.share").shareText(text)
            return True
        except Exception as e:
            log.warning("Share text failed: %s", e)
            return False

    def open_file_on_phone(self, filepath):
        """openFile(s file) — open a file that's already on the phone"""
        try:
            self._iface(self._dev("share"),
                        "org.kde.kdeconnect.device.share").openFile(filepath)
            return True
        except Exception as e:
            log.warning("Open file on phone failed %s: %s", filepath, e)
            return False

    # ── Clipboard ──────────────────────────────────────────────
    # Method:   sendClipboard()  — pushes NixOS clipboard to phone
    # Signal:   autoShareDisabledChanged(b b)
    # Property: readonly b isAutoShareDisabled
    def send_clipboard_to_phone(self):
        """Push current NixOS clipboard content to phone"""
        try:
            self._iface(self._dev("clipboard"),
                        "org.kde.kdeconnect.device.clipboard").sendClipboard()
            return True
        except Exception as e:
            log.warning("Send clipboard failed: %s", e)
            return False

    def get_clipboard_autoshare(self):
        """Returns True if auto-share is ENABLED (isAutoShareDisabled=False)"""
        try:
            v = self._prop("clipboard",
                           "org.kde.kdeconnect.device.clipboard",
                           "isAutoShareDisabled")
            return not bool(v)
        except Exception:
            log.debug("Clipboard autoshare read failed", exc_info=True)
            return False

    # ── Run Commands ───────────────────────────────────────────
    def get_commands(self):
        try:
            import json
            # runcommand plugin path varies — try direct
            path = self._dev("runcommand")
            iface = self._iface(path, "org.kde.kdeconnect.device.runcommand")
            raw = iface.commandList()
            return json.loads(str(raw))
        except Exception as e:
            log.warning("Command list fetch failed: %s", e)
            return {}

    def run_command(self, command_name):
        try:
            import json
            path = self._dev("runcommand")
            iface = self._iface(path, "org.kde.kdeconnect.device.runcommand")
            cmds = json.loads(str(iface.commandList()))
            for key, cmd in cmds.items():
                if cmd.get("name") == command_name:
                    iface.triggerCommand(key)
                    return True
            log.warning("Command '%s' not found in %s", command_name, [c.get("name") for c in cmds.values()])
            return False
        except Exception as e:
            log.warning("Run command failed %s: %s", command_name, e)
            return False

    def run_command_by_key(self, key):
        try:
            path = self._dev("runcommand")
            iface = self._iface(path, "org.kde.kdeconnect.device.runcommand")
            iface.triggerCommand(key)
            return True
        except Exception as e:
            log.warning("Run command by key failed %s: %s", key, e)
            return False

    # ── Contacts ───────────────────────────────────────────────
    def sync_contacts(self):
        try:
            self._iface(self._dev("contacts"),
                        "org.kde.kdeconnect.device.contacts"
                        ).synchronizeRemoteWithLocal()
            return True
        except Exception as e:
            log.warning("Contacts sync failed: %s", e)
            return False

    def get_cached_contacts(self):
        """Read contacts from KDE Connect's local vCard cache"""
        import os, glob
        contacts = []
        cache_dirs = [
            os.path.expanduser(f"~/.local/share/kdeconnect/{self.device_id}/"),
            os.path.expanduser(f"~/.local/share/kdeconnect/"),
            os.path.expanduser(f"~/.local/share/kpeoplevcard/kdeconnect-{self.device_id}/"),
            os.path.expanduser("~/.local/share/kpeoplevcard/"),
        ]
        for d in cache_dirs:
            vcards = glob.glob(os.path.join(d, "*.vcf")) + \
                     glob.glob(os.path.join(d, "**/*.vcf"), recursive=True)
            for path in vcards[:500]:
                try:
                    with open(path) as f:
                        content = f.read()
                    name, phone = "", ""
                    for line in content.splitlines():
                        if line.startswith("FN:"):
                            name = line[3:].strip()
                        elif line.startswith("TEL") and ":" in line:
                            phone = line.split(":")[-1].strip()
                    if name or phone:
                        contacts.append({"name": name, "phone": phone})
                except Exception:
                    log.debug("Failed reading cached contact path=%s", path, exc_info=True)
        return contacts

    # ── SFTP ───────────────────────────────────────────────────
    def mount_sftp(self):
        try:
            self._iface(self._dev("sftp"),
                        "org.kde.kdeconnect.device.sftp").mountAndWait()
            return True
        except Exception as e:
            log.warning("SFTP mount failed: %s", e)
            return False

    def get_sftp_path(self):
        try:
            return str(self._prop("sftp", "org.kde.kdeconnect.device.sftp", "mountPoint") or "")
        except Exception:
            log.debug("SFTP mount path read failed", exc_info=True)
            return ""

    def get_receive_path(self):
        """Best-effort local path used by KDE Connect for received files."""
        candidates = [
            os.path.expanduser("~/.config/kdeconnectrc"),
            os.path.expanduser("~/.config/kdeconnect/kdeconnectrc"),
        ]
        for path in candidates:
            try:
                if not os.path.exists(path):
                    continue
                cfg = configparser.ConfigParser()
                cfg.read(path, encoding="utf-8")
                for section in cfg.sections():
                    lower_section = section.lower()
                    if "share" not in lower_section and "kdeconnect" not in lower_section:
                        continue
                    for key in ("downloadpath", "download_path", "incomingpath", "incoming_path", "savepath"):
                        if cfg.has_option(section, key):
                            value = (cfg.get(section, key) or "").strip()
                            if value:
                                return os.path.expanduser(value)
            except Exception:
                continue

        try:
            out = subprocess.check_output(["xdg-user-dir", "DOWNLOAD"], text=True, timeout=2).strip()
            if out:
                return os.path.expanduser(out)
        except Exception:
            pass
        return os.path.expanduser("~/Downloads")

    @staticmethod
    def suppress_native_notification_popups(enable: bool = True) -> bool:
        return kde_notifications.suppress_native_notification_popups(enable)

    # ── Telephony signal listener ──────────────────────────────
    def connect_call_signal(self, callback):
        return kde_signals.connect_call_signal(self, callback)

    # ── Notification signal listeners ──────────────────────────
    def connect_notification_signal(self, posted_cb=None, removed_cb=None, updated_cb=None, all_removed_cb=None):
        return kde_signals.connect_notification_signal(self, posted_cb, removed_cb, updated_cb, all_removed_cb)

    def connect_clipboard_signal(self, received_cb=None):
        return kde_signals.connect_clipboard_signal(self, received_cb)

    # ── Battery signal ─────────────────────────────────────────
    def connect_battery_signal(self, callback):
        return kde_signals.connect_battery_signal(self, callback)

    # ── Device reachability ────────────────────────────────────
    def is_reachable(self) -> bool | None:
        """Return True if device reachable, False if not, None if D-Bus unavailable."""
        try:
            daemon_obj = self.bus.get_object(BUS_NAME, "/modules/kdeconnect", introspect=False)
            daemon = dbus.Interface(daemon_obj, "org.kde.kdeconnect.daemon")
            # Reachable + paired devices from kdeconnectd itself.
            reachable = list(daemon.devices(True, True) or [])
            if not reachable:
                return False
            target_id = str(self.device_id or "").strip()
            if not target_id:
                return True
            return target_id in {str(x) for x in reachable}
        except Exception:
            # Return None so callers can surface Unknown instead of false-negative Unreachable.
            return None

    def get_device_name(self):
        try:
            obj   = self._obj(self._dev())
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return str(props.Get("org.kde.kdeconnect.device", "name"))
        except:
            return str(setting("device_name", "Phone") or "Phone")


# ── Module-level helpers for in-app health / watchdog ────────────────────────

def trigger_refresh() -> bool:
    """Run `kdeconnect-cli --refresh` and return True if it exited 0.

    Non-blocking wrapper; used by the in-app health probe before re-checking
    reachability.  Returns False on timeout or non-zero exit.
    """
    try:
        res = subprocess.run(
            ["kdeconnect-cli", "--refresh"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        ok = res.returncode == 0
        if not ok:
            log.debug("kdeconnect-cli --refresh rc=%s stderr=%s", res.returncode, (res.stderr or "").strip()[:200])
        return ok
    except Exception as exc:
        log.debug("kdeconnect-cli --refresh raised: %s", exc)
        return False


def kde_health_probe(device_id: str = "") -> dict:
    """One-shot health check: returns a dict suitable for state['kde_health'].

    Keys:
        status:       "ok" | "degraded" | "unknown"
        reachable:    True | False | None   (None = D-Bus unavailable)
        refresh_ok:   True | False | None   (None = not attempted)
        checked_at:   int epoch-ms
        device_id:    str device_id used for check

    When device is not reachable, trigger_refresh() is called once and
    reachability is re-probed.  The refresh attempt result is stored in
    refresh_ok.  The final status is based on the second probe.
    """
    import time as _time

    _did = str(device_id or runtime_config.device_id() or "").strip()
    try:
        kc = KDEConnect()
        if _did:
            kc.device_id = _did
        first_check = kc.is_reachable()
    except Exception as exc:
        log.debug("kde_health_probe: KDEConnect() failed: %s", exc)
        first_check = None

    if first_check is True:
        return {
            "status": "ok",
            "reachable": True,
            "refresh_ok": None,
            "checked_at": int(_time.time() * 1000),
            "device_id": _did,
        }

    # Not reachable or D-Bus unavailable — attempt a refresh then re-probe.
    refresh_ok = trigger_refresh()

    try:
        kc2 = KDEConnect()
        if _did:
            kc2.device_id = _did
        second_check = kc2.is_reachable()
    except Exception:
        second_check = None

    if second_check is True:
        status = "ok"
    elif second_check is None or first_check is None:
        status = "unknown"
    else:
        status = "degraded"

    return {
        "status": status,
        "reachable": second_check,
        "refresh_ok": refresh_ok,
        "checked_at": int(_time.time() * 1000),
        "device_id": _did,
    }
