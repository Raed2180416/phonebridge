"""Signal registration helpers for the KDE Connect facade."""

from __future__ import annotations

BUS_NAME = "org.kde.kdeconnect"


def connect_call_signal(kc, callback):
    telephony_path = kc._dev("telephony")
    device_path = kc._dev()

    attempts = (
        (
            "primary",
            {
                "signal_name": "callReceived",
                "dbus_interface": "org.kde.kdeconnect.device.telephony",
                "bus_name": BUS_NAME,
                "path": telephony_path,
            },
        ),
        (
            "fallback-no-busname",
            {
                "signal_name": "callReceived",
                "dbus_interface": "org.kde.kdeconnect.device.telephony",
                "path": telephony_path,
            },
        ),
        (
            "fallback-device-path",
            {
                "signal_name": "callReceived",
                "dbus_interface": "org.kde.kdeconnect.device.telephony",
                "path": device_path,
            },
        ),
    )
    for label, kwargs in attempts:
        try:
            kc._add_signal_receiver(callback, **kwargs)
            kc.log.info("Call signal registered (%s) path=%s", label, kwargs.get("path"))
            return True
        except Exception as exc:
            kc.log.warning("Call signal %s hook failed: %s", label, exc)
    kc.log.error("All call signal registrations failed — incoming call popup will not work via D-Bus")
    return False


def connect_notification_signal(kc, posted_cb=None, removed_cb=None, updated_cb=None, all_removed_cb=None):
    try:
        if posted_cb:
            kc._add_signal_receiver(
                posted_cb,
                signal_name="notificationPosted",
                dbus_interface="org.kde.kdeconnect.device.notifications",
                bus_name=BUS_NAME,
                path=kc._dev("notifications"),
            )
        if removed_cb:
            kc._add_signal_receiver(
                removed_cb,
                signal_name="notificationRemoved",
                dbus_interface="org.kde.kdeconnect.device.notifications",
                bus_name=BUS_NAME,
                path=kc._dev("notifications"),
            )
        if updated_cb:
            kc._add_signal_receiver(
                updated_cb,
                signal_name="notificationUpdated",
                dbus_interface="org.kde.kdeconnect.device.notifications",
                bus_name=BUS_NAME,
                path=kc._dev("notifications"),
            )
        if all_removed_cb:
            kc._add_signal_receiver(
                all_removed_cb,
                signal_name="allNotificationsRemoved",
                dbus_interface="org.kde.kdeconnect.device.notifications",
                bus_name=BUS_NAME,
                path=kc._dev("notifications"),
            )
        return True
    except Exception as exc:
        kc.log.warning("Notification signal hook failed: %s", exc)
        return False


def connect_clipboard_signal(kc, received_cb=None):
    try:
        if received_cb:
            kc._add_signal_receiver(
                received_cb,
                signal_name="clipboardReceived",
                dbus_interface="org.kde.kdeconnect.device.clipboard",
                bus_name=BUS_NAME,
                path=kc._dev("clipboard"),
            )
            kc._add_signal_receiver(
                received_cb,
                signal_name="clipboardChanged",
                dbus_interface="org.kde.kdeconnect.device.clipboard",
                bus_name=BUS_NAME,
                path=kc._dev("clipboard"),
            )
            kc._add_signal_receiver(
                received_cb,
                signal_name="PropertiesChanged",
                dbus_interface="org.freedesktop.DBus.Properties",
                bus_name=BUS_NAME,
                path=kc._dev("clipboard"),
            )
        return True
    except Exception as exc:
        kc.log.warning("Clipboard signal hook failed: %s", exc)
        return False


def connect_battery_signal(kc, callback):
    try:
        kc._add_signal_receiver(
            callback,
            signal_name="refreshed",
            dbus_interface="org.kde.kdeconnect.device.battery",
            bus_name=BUS_NAME,
            path=kc._dev("battery"),
        )
        return True
    except Exception as exc:
        kc.log.warning("Battery signal hook failed: %s", exc)
        return False
