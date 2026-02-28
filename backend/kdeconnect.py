"""KDE Connect D-Bus interface — all plugins"""
import logging
import os
import subprocess
import configparser
from pathlib import Path

import dbus
import dbus.mainloop.glib
from gi.repository import GLib
from backend.settings_store import get as setting

DEVICE_ID = "a9fe30c209da40d4bddce484a2c4112a"
BUS_NAME  = "org.kde.kdeconnect"
log = logging.getLogger(__name__)

def get_bus():
    return dbus.SessionBus()

class KDEConnect:
    def __init__(self):
        self.device_id = setting("device_id", DEVICE_ID)
        self._bus = None

    @property
    def bus(self):
        if self._bus is None:
            self._bus = dbus.SessionBus()
        return self._bus

    def _obj(self, path):
        return self.bus.get_object(BUS_NAME, path, introspect=False)

    def _iface(self, path, iface):
        return dbus.Interface(self._obj(path), iface)

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
        except:
            return "Unknown"

    def get_signal_strength(self):
        try:
            s = self._prop("connectivity_report",
                           "org.kde.kdeconnect.device.connectivity_report",
                           "cellularNetworkStrength")
            return int(s) if s is not None else -1
        except:
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
        try:
            iface = self._iface(self._dev("notifications"),
                                "org.kde.kdeconnect.device.notifications")
            ids = iface.activeNotifications(timeout=3)
            result = []
            for nid in ids:
                npath = self._dev(f"notifications/{nid}")
                props = dbus.Interface(self._obj(npath),
                                       "org.freedesktop.DBus.Properties")
                ns = "org.kde.kdeconnect.device.notifications.notification"
                try:
                    def _get_prop(name, default=None):
                        try:
                            return props.Get(ns, name)
                        except Exception:
                            return default

                    def _read_text():
                        for key in ("text", "notitext", "ticker"):
                            try:
                                value = props.Get(ns, key)
                                if value:
                                    return str(value)
                            except Exception:
                                continue
                        return ""

                    result.append({
                        "id":          str(nid),
                        "app":         str(_get_prop("appName", "App")),
                        "title":       str(_get_prop("title", "Notification")),
                        "text":        _read_text(),
                        "dismissable": bool(_get_prop("dismissable", True)),
                        "replyId":     str(_get_prop("replyId", "") or ""),
                        "actions":     list(_get_prop("actions", []) or []),
                    })
                except Exception as e2:
                    log.warning("Notification property read failed %s: %s", nid, e2)
                    result.append({"id": str(nid), "app": "App", "title": "Notification",
                                   "text": "", "dismissable": True,
                                   "replyId": "", "actions": []})
            return result
        except Exception as e:
            log.warning("Notifications fetch failed: %s", e)
            return []

    def dismiss_notification(self, notif_id):
        """Dismiss via the per-notification object's dismiss() method"""
        ok = False
        try:
            npath = self._dev(f"notifications/{notif_id}")
            self._iface(npath,
                        "org.kde.kdeconnect.device.notifications.notification"
                        ).dismiss()
            ok = True
        except Exception as e:
            log.warning("Notification dismiss failed %s: %s", notif_id, e)

        # Some notifications respond only to sendAction() paths.
        if not ok:
            for candidate in ("dismiss", "clear", "default"):
                try:
                    self._iface(
                        self._dev("notifications"),
                        "org.kde.kdeconnect.device.notifications",
                    ).sendAction(str(notif_id), candidate)
                    ok = True
                    break
                except Exception:
                    continue
        return ok

    def open_notification_reply(self, notif_id):
        """Trigger reply flow for a notification using per-notification reply()."""
        try:
            npath = self._dev(f"notifications/{notif_id}")
            self._iface(
                npath,
                "org.kde.kdeconnect.device.notifications.notification",
            ).reply()
            return True
        except Exception as e:
            log.warning("Notification quick-reply open failed %s: %s", notif_id, e)
            return False

    def reply_notification(self, reply_id, message):
        """sendReply(s replyId, s message) on the notifications interface"""
        try:
            self._iface(self._dev("notifications"),
                        "org.kde.kdeconnect.device.notifications"
                        ).sendReply(reply_id, message)
            return True
        except Exception as e:
            log.warning("Notification reply failed %s: %s", reply_id, e)
            return False

    def send_notification_action(self, key, action):
        """sendAction(s key, s action) on the notifications interface"""
        try:
            self._iface(self._dev("notifications"),
                        "org.kde.kdeconnect.device.notifications"
                        ).sendAction(key, action)
            return True
        except Exception as e:
            log.warning("Notification action failed %s/%s: %s", key, action, e)
            return False

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
        except:
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
                except:
                    pass
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
        except:
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
        """
        Disable KDE Connect's own desktop popup event so only PhoneBridge-mirrored
        notifications are shown in the shell panel.
        """
        target_action = "None" if enable else "Popup"
        cfg_path = Path.home() / ".config" / "knotifications6" / "kdeconnect.notifyrc"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)

        # KConfig is case-sensitive for key names in practice; keep exact casing.
        desired = (
            "[Event/notification]\n"
            f"Action={target_action}\n"
            "ShowInHistory=false\n"
        )
        current = ""
        if cfg_path.exists():
            try:
                current = cfg_path.read_text(encoding="utf-8")
            except Exception:
                current = ""
        if current.strip() == desired.strip():
            return False
        cfg_path.write_text(desired, encoding="utf-8")

        # Best-effort daemon refresh; okay if unavailable.
        try:
            subprocess.run(
                ["systemctl", "--user", "try-restart", "kdeconnectd.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
            )
        except Exception:
            pass
        return True

    # ── Telephony signal listener ──────────────────────────────
    def connect_call_signal(self, callback):
        """callback(event, number, contact_name)
           event: 'ringing', 'talking', 'missed_call'
        """
        try:
            self.bus.add_signal_receiver(
                callback,
                signal_name="callReceived",
                dbus_interface="org.kde.kdeconnect.device.telephony",
                bus_name=BUS_NAME,
                path=self._dev("telephony"),
            )
            return True
        except Exception as e:
            log.warning("Call signal hook failed: %s", e)
            return False

    # ── Notification signal listeners ──────────────────────────
    def connect_notification_signal(self, posted_cb=None, removed_cb=None, updated_cb=None, all_removed_cb=None):
        try:
            if posted_cb:
                self.bus.add_signal_receiver(
                    posted_cb,
                    signal_name="notificationPosted",
                    dbus_interface="org.kde.kdeconnect.device.notifications",
                    bus_name=BUS_NAME,
                    path=self._dev("notifications"),
                )
            if removed_cb:
                self.bus.add_signal_receiver(
                    removed_cb,
                    signal_name="notificationRemoved",
                    dbus_interface="org.kde.kdeconnect.device.notifications",
                    bus_name=BUS_NAME,
                    path=self._dev("notifications"),
                )
            if updated_cb:
                self.bus.add_signal_receiver(
                    updated_cb,
                    signal_name="notificationUpdated",
                    dbus_interface="org.kde.kdeconnect.device.notifications",
                    bus_name=BUS_NAME,
                    path=self._dev("notifications"),
                )
            if all_removed_cb:
                self.bus.add_signal_receiver(
                    all_removed_cb,
                    signal_name="allNotificationsRemoved",
                    dbus_interface="org.kde.kdeconnect.device.notifications",
                    bus_name=BUS_NAME,
                    path=self._dev("notifications"),
                )
            return True
        except Exception as e:
            log.warning("Notification signal hook failed: %s", e)
            return False

    def connect_clipboard_signal(self, received_cb=None):
        try:
            if received_cb:
                self.bus.add_signal_receiver(
                    received_cb,
                    signal_name="clipboardReceived",
                    dbus_interface="org.kde.kdeconnect.device.clipboard",
                    bus_name=BUS_NAME,
                    path=self._dev("clipboard"),
                )
            return True
        except Exception as e:
            log.warning("Clipboard signal hook failed: %s", e)
            return False

    # ── Battery signal ─────────────────────────────────────────
    def connect_battery_signal(self, callback):
        try:
            self.bus.add_signal_receiver(
                callback,
                signal_name="refreshed",
                dbus_interface="org.kde.kdeconnect.device.battery",
                bus_name=BUS_NAME,
                path=self._dev("battery"),
            )
            return True
        except Exception as e:
            log.warning("Battery signal hook failed: %s", e)
            return False

    # ── Device reachability ────────────────────────────────────
    def is_reachable(self):
        try:
            obj   = self._obj(self._dev())
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return bool(props.Get("org.kde.kdeconnect.device", "isReachable"))
        except:
            return True  # assume reachable if can't check

    def get_device_name(self):
        try:
            obj   = self._obj(self._dev())
            props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
            return str(props.Get("org.kde.kdeconnect.device", "name"))
        except:
            return "Nothing Phone 3a Pro"
