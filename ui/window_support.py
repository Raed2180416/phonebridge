"""Window support widgets and D-Bus bridge helpers."""

from __future__ import annotations

import logging

try:
    import dbus  # type: ignore
    import dbus.mainloop.glib  # type: ignore
    from gi.repository import GLib  # type: ignore

    _HAVE_DBUS = True
except Exception:
    dbus = None
    GLib = None
    _HAVE_DBUS = False

from PyQt6.QtCore import QEasingCurve, QObject, QPropertyAnimation, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QPushButton

from ui.theme import VIOLET

log = logging.getLogger(__name__)


class DBusSignalBridge(QObject):
    """Runs GLib mainloop in a thread, bridges D-Bus signals to Qt signals."""

    call_received = pyqtSignal(str, str, str)
    notif_changed = pyqtSignal(object)
    clipboard_received = pyqtSignal(str)
    battery_updated = pyqtSignal(int, bool)

    def __init__(self):
        super().__init__()
        self._loop = None
        self._thread = None
        self._bus = None
        self._kc = None
        self._running = False
        self._stopping = False

    def start(self):
        import threading

        if self._running:
            return
        if not _HAVE_DBUS:
            raise RuntimeError("python-dbus/gi not available; KDE integration disabled")
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._loop = GLib.MainLoop()
        self._running = True
        self._stopping = False

        from backend.kdeconnect import KDEConnect

        self._kc = KDEConnect()
        self._register_all_signals(self._kc)

        try:
            self._bus = dbus.SessionBus()
            self._bus.watch_name_owner("org.kde.kdeconnect", self._on_kde_name_owner_changed)
            log.info("Watching NameOwnerChanged for org.kde.kdeconnect")
        except Exception as exc:
            log.warning("NameOwnerChanged watch failed: %s", exc)

        self._thread = threading.Thread(target=self._loop.run, daemon=True, name="pb-dbus-glib")
        self._thread.start()
        log.info("DBusSignalBridge started (GLib loop in background thread)")

    def _register_all_signals(self, kc):
        kc.connect_call_signal(self._on_call)
        kc.connect_notification_signal(
            posted_cb=lambda notif_id: self._on_notif_changed(notif_id, "posted"),
            removed_cb=lambda notif_id: self._on_notif_changed(notif_id, "removed"),
            updated_cb=lambda notif_id: self._on_notif_changed(notif_id, "updated"),
            all_removed_cb=self._on_notif_all_removed,
        )
        kc.connect_battery_signal(self._on_battery)
        kc.connect_clipboard_signal(self._on_clipboard_received)

    def _on_kde_name_owner_changed(self, new_owner):
        if (not self._running) or self._stopping:
            return
        if not new_owner:
            log.info("org.kde.kdeconnect lost from bus (daemon stopped)")
            return
        log.info("org.kde.kdeconnect re-acquired by %s — re-registering signals", new_owner)
        try:
            old_kc = getattr(self, "_kc", None)
            if old_kc is not None and hasattr(old_kc, "disconnect_all_signals"):
                old_kc.disconnect_all_signals()
            from backend.kdeconnect import KDEConnect

            self._kc = KDEConnect()
            self._register_all_signals(self._kc)
            KDEConnect.suppress_native_notification_popups(True)
        except Exception:
            log.exception("Failed to re-register signals after NameOwnerChanged")

    def stop(self):
        if not self._running and self._thread is None and self._loop is None:
            return
        self._stopping = True
        old_kc = getattr(self, "_kc", None)
        if old_kc is not None and hasattr(old_kc, "disconnect_all_signals"):
            try:
                old_kc.disconnect_all_signals()
            except Exception:
                log.exception("Failed to disconnect KDE signal receivers")
        if self._loop is not None:
            try:
                self._loop.quit()
            except Exception:
                log.exception("Failed stopping DBus GLib loop")
        thread = getattr(self, "_thread", None)
        if thread is not None:
            try:
                thread.join(timeout=1.5)
            except Exception:
                log.exception("Failed joining DBus GLib loop thread")
        self._thread = None
        self._loop = None
        self._kc = None
        self._bus = None
        self._running = False
        self._stopping = False

    def _on_call(self, *args):
        if not self._running:
            return
        event = str(args[0]) if len(args) > 0 else ""
        number = str(args[1]) if len(args) > 1 else ""
        contact_name = str(args[2]) if len(args) > 2 else number
        try:
            self.call_received.emit(event, number, contact_name)
        except RuntimeError:
            self._running = False

    def _on_notif_changed(self, notif_id, reason="updated"):
        if not self._running:
            return
        try:
            self.notif_changed.emit({"id": str(notif_id), "reason": str(reason or "updated")})
        except RuntimeError:
            self._running = False

    def _on_notif_all_removed(self):
        if not self._running:
            return
        try:
            self.notif_changed.emit({"id": "all_removed", "reason": "all_removed"})
        except RuntimeError:
            self._running = False

    def _on_battery(self, is_charging, charge):
        if not self._running:
            return
        try:
            self.battery_updated.emit(int(charge), bool(is_charging))
        except RuntimeError:
            self._running = False

    def _on_clipboard_received(self, *args):
        if not self._running:
            return
        text = ""

        def _extract(value):
            try:
                if isinstance(value, (bytes, bytearray)):
                    return value.decode("utf-8", errors="ignore").strip()
            except Exception:
                pass
            if isinstance(value, dict):
                for key in ("text", "clipboard", "content", "value"):
                    if key in value and value.get(key):
                        return str(value.get(key))
                return ""
            if isinstance(value, (list, tuple)):
                for item in value:
                    s = _extract(item)
                    if s:
                        return s
                return ""
            s = str(value or "").strip()
            if s in {"", "True", "False", "{}", "[]", "()"}:
                return ""
            return s

        for arg in args:
            s = _extract(arg)
            if s:
                text = s
                break
        if not text and args:
            text = _extract(args[0])
        try:
            self.clipboard_received.emit(text)
        except RuntimeError:
            self._running = False


class SidebarIconButton(QPushButton):
    """Sidebar icon button with soft bg animation and deterministic paint."""

    def __init__(self, text="", parent=None, font_size: int = 15):
        super().__init__("", parent)
        self._icon_text = str(text or "")
        self._font_size = max(12, int(font_size))
        self._active = False
        self._hovered = False
        self._bg_opacity = 0.0
        self._anim = QPropertyAnimation(self, b"bgOpacity", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 0;
                margin: 0;
            }
            QPushButton:focus {
                outline: none;
                border: none;
            }
            """
        )

    def getBgOpacity(self):
        return self._bg_opacity

    def setBgOpacity(self, value):
        self._bg_opacity = float(max(0.0, min(1.0, value)))
        self.update()

    bgOpacity = pyqtProperty(float, fget=getBgOpacity, fset=setBgOpacity)

    def set_active(self, active: bool):
        self._active = bool(active)
        target = 1.0 if self._active else (1.0 if self._hovered else 0.0)
        self._animate_to(target)

    def enterEvent(self, event):
        self._hovered = True
        if not self._active:
            self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._active:
            self._animate_to(0.0)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def _animate_to(self, target: float):
        if abs(self._bg_opacity - target) < 0.001:
            return
        self._anim.stop()
        self._anim.setStartValue(self._bg_opacity)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        if self._bg_opacity > 0.0:
            bg = QColor("#1a1e28")
            bg.setAlphaF(self._bg_opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, 8, 8)

        if self._active:
            icon_color = QColor(VIOLET)
        elif self._bg_opacity > 0.05:
            icon_color = QColor("#8892a8")
        else:
            icon_color = QColor("#4e5a72")

        painter.setPen(icon_color)
        font = QFont()
        font.setPointSize(self._font_size)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._icon_text)
        painter.end()
