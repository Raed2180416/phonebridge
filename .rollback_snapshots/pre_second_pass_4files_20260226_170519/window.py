"""PhoneBridge main window"""
import dbus
import dbus.mainloop.glib
from gi.repository import GLib
import logging
import time
import threading

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout,
                              QVBoxLayout, QPushButton, QStackedWidget,
                              QLabel, QScrollArea, QApplication, QFrame)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from ui.theme import (
    TEAL,
    BORDER,
    TEXT_DIM,
    get_app_style,
    set_surface_alpha,
    refresh_card_styles,
    set_theme_name,
)
import ui.theme as theme
from ui.motion import fade_in
from backend.state import state
import backend.settings_store as settings
from backend.clipboard_history import sanitize_clipboard_history

log = logging.getLogger(__name__)

# Pages
from ui.pages.dashboard  import DashboardPage
from ui.pages.messages   import MessagesPage
from ui.pages.calls      import CallsPage
from ui.pages.files      import FilesPage
from ui.pages.mirror     import MirrorPage
from ui.pages.sync       import SyncPage
from ui.pages.network    import NetworkPage
from ui.pages.settings   import SettingsPage

PAGES = [
    ("⌂",  "Dashboard",  DashboardPage,  "dashboard"),
    ("✉",  "Messages",   MessagesPage,   "messages"),
    ("☎",  "Calls",      CallsPage,      "calls"),
    ("▤",  "Files",      FilesPage,      "files"),
    ("▣",  "Mirror",     MirrorPage,     "mirror"),
    ("↺",  "Sync",       SyncPage,       "sync"),
    ("◈",  "Network",    NetworkPage,    "network"),
    ("⚙",  "Settings",   SettingsPage,   "settings"),
]

# Separator before Network (index 6)
SEPARATOR_BEFORE = 6


class DBusSignalBridge(QObject):
    """Runs GLib mainloop in a thread, bridges D-Bus signals to Qt signals"""
    call_received   = pyqtSignal(str, str, str)  # event, number, name
    notif_changed   = pyqtSignal(str)             # notif_id/reason
    clipboard_received = pyqtSignal(str)          # clipboard text
    battery_updated = pyqtSignal(int, bool)       # charge, is_charging

    def __init__(self):
        super().__init__()
        self._loop = None
        self._running = False

    def start(self):
        import threading
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._loop = GLib.MainLoop()
        self._running = True

        from backend.kdeconnect import KDEConnect
        kc = KDEConnect()
        kc.connect_call_signal(self._on_call)
        kc.connect_notification_signal(
            posted_cb=self._on_notif_changed,
            removed_cb=self._on_notif_changed,
            updated_cb=self._on_notif_changed,
            all_removed_cb=self._on_notif_all_removed,
        )
        kc.connect_battery_signal(self._on_battery)
        kc.connect_clipboard_signal(self._on_clipboard_received)

        t = threading.Thread(target=self._loop.run, daemon=True)
        t.start()

    def stop(self):
        self._running = False
        if self._loop is not None:
            try:
                self._loop.quit()
            except Exception:
                pass

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

    def _on_notif_changed(self, notif_id):
        if not self._running:
            return
        try:
            self.notif_changed.emit(str(notif_id))
        except RuntimeError:
            self._running = False

    def _on_notif_all_removed(self):
        if not self._running:
            return
        try:
            self.notif_changed.emit("all_removed")
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
        for arg in args:
            s = str(arg)
            if s and s not in {"True", "False"}:
                text = s
                break
        if not text and args:
            text = str(args[0])
        try:
            self.clipboard_received.emit(text)
        except RuntimeError:
            self._running = False


class PhoneBridgeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhoneBridge")
        self.setMinimumSize(960, 640)
        self.resize(1140, 740)
        self._current_opacity_pct = int(settings.get("window_opacity", 94) or 94)
        self._current_motion = str(settings.get("motion_level", "rich") or "rich")
        self._current_theme = str(settings.get("theme_name", "slate") or "slate")
        from ui.theme import set_motion_level
        set_motion_level(self._current_motion)
        set_theme_name(self._current_theme)
        set_surface_alpha(self._current_opacity_pct)
        self.setStyleSheet(get_app_style(self._current_opacity_pct))
        opacity = int(settings.get("window_opacity", 94) or 94)
        self.setWindowOpacity(max(0.72, min(1.0, opacity / 100.0)))

        self._page_map = {}
        self._active_popups = []
        self._last_clipboard_text = ""
        self._clipboard_history = []
        self._last_call_key = ""
        self._last_call_at = 0.0
        self._suspend_poll_until = 0.0
        self._force_quit = False
        self._build_ui()
        self._build_toast_layer()
        self._clipboard_history = sanitize_clipboard_history(settings.get("clipboard_history", []) or [])
        settings.set("clipboard_history", self._clipboard_history)
        state.set("clipboard_history", self._clipboard_history)
        self._center_on_screen()
        self._start_signal_bridge()
        QTimer.singleShot(1800, self._auto_connect_bluetooth)
        clip = QApplication.clipboard()
        clip.dataChanged.connect(self._on_local_clipboard_changed)
        self._on_local_clipboard_changed()

        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(8000)
        state.subscribe("ui_toast_queue", self._on_toast_queue)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        # ── Sidebar ──────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(72)
        sidebar.setStyleSheet(theme.SIDEBAR_STYLE)
        self._sidebar = sidebar
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(12,16,12,16)
        sb.setSpacing(8)

        logo = QLabel("⌁")
        logo.setObjectName("sb-logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedHeight(42)
        logo.setCursor(Qt.CursorShape.PointingHandCursor)
        logo.mousePressEvent = lambda _: self.set_page(0)
        sb.addWidget(logo)
        sb.addSpacing(8)

        sep_widget = QWidget()
        sep_widget.setFixedHeight(1)
        sep_widget.setStyleSheet(f"background:{BORDER};border:none;")
        sb.addWidget(sep_widget)
        sb.addSpacing(8)

        self._sb_buttons = {}
        self._stack = QStackedWidget()

        for i, (icon, name, PageClass, page_id) in enumerate(PAGES):
            if i == SEPARATOR_BEFORE:
                sb.addSpacing(8)
                sep2 = QWidget()
                sep2.setFixedHeight(1)
                sep2.setStyleSheet(f"background:{BORDER};border:none;")
                sb.addWidget(sep2)
                sb.addSpacing(8)

            if page_id != "dashboard":
                btn = QPushButton(icon)
                btn.setObjectName("sb-item")
                btn.setToolTip(name)
                btn.setFixedSize(44,44)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                btn.setAutoDefault(False)
                btn.clicked.connect(lambda _, idx=i: self.set_page(idx))
                sb.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
                self._sb_buttons[i] = btn

            scroll = QScrollArea()
            scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page_inst = PageClass()
            scroll.setWidget(page_inst)
            self._stack.addWidget(scroll)
            self._page_map[page_id] = (i, page_inst)

        sb.addStretch()
        root.addWidget(sidebar)
        root.addWidget(self._stack)

        self.set_page(0)

    def set_page(self, index):
        self._stack.setCurrentIndex(index)
        current = self._stack.currentWidget()
        if current:
            fade_in(current, level=self._current_motion)
        for page_index, btn in self._sb_buttons.items():
            btn.setProperty("active", page_index == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def go_to(self, page_id):
        if page_id in self._page_map:
            self.set_page(self._page_map[page_id][0])

    def get_page(self, page_id):
        if page_id in self._page_map:
            return self._page_map[page_id][1]
        return None

    def _rebuild_pages_for_theme(self):
        active_page_id = None
        if hasattr(self, "_stack") and self._stack is not None:
            current_idx = self._stack.currentIndex()
            for pid, (idx, _) in self._page_map.items():
                if idx == current_idx:
                    active_page_id = pid
                    break
        old_central = self.centralWidget()
        if old_central is not None:
            old_central.setParent(None)
            old_central.deleteLater()
        self._page_map = {}
        self._sb_buttons = {}
        self._build_ui()
        if active_page_id and active_page_id in self._page_map:
            self.go_to(active_page_id)

    def show_and_raise(self):
        if not self._poll_timer.isActive():
            self._poll_timer.start(8000)
        self.show()
        self.raise_()
        self.activateWindow()

    def run_startup_check(self):
        from backend.startup_check import StartupChecker
        StartupChecker(self).run_and_show()

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2,
        )

    def _start_signal_bridge(self):
        if not settings.get("kde_integration_enabled", True):
            log.info("KDE integration disabled by setting; DBus bridge not started")
            return
        try:
            self._bridge = DBusSignalBridge()
            self._bridge.call_received.connect(self._on_call_received)
            self._bridge.notif_changed.connect(self._on_notif_changed)
            self._bridge.battery_updated.connect(self._on_battery_updated)
            self._bridge.clipboard_received.connect(self._on_clipboard_received)
            self._bridge.start()
        except Exception as e:
            log.exception("Signal bridge error: %s", e)

    def _on_call_received(self, event, number, contact_name):
        normalized_event = self._normalize_call_event(event)
        call_key = f"{normalized_event}|{number}|{contact_name}"
        now = time.time()
        if call_key == self._last_call_key and (now - self._last_call_at) < 0.8:
            return
        self._last_call_key = call_key
        self._last_call_at = now
        self._suspend_poll_until = now + 2.0
        log.info(
            "Signal callReceived raw_event=%s normalized_event=%s number=%s contact=%s",
            event, normalized_event, number, contact_name
        )
        if settings.get("suppress_calls"):
            return
        for p in list(self._active_popups):
            try:
                p.close()
            except Exception:
                pass
        from ui.components.call_popup import CallPopup
        try:
            popup = CallPopup(normalized_event, number, contact_name, self)
            popup.destroyed.connect(lambda _: self._active_popups.remove(popup) if popup in self._active_popups else None)
            self._active_popups.append(popup)
            popup.show()
        except Exception:
            log.exception("Failed to render call popup")
        state.set("call_state", {"event": normalized_event, "number": number, "contact_name": contact_name})
        state.set("call_ui_state", {
            "status": "ringing" if normalized_event == "ringing" else normalized_event,
            "number": number,
            "contact_name": contact_name,
            "updated_at": int(time.time() * 1000),
        })

        # Also refresh calls page
        calls_page = self.get_page("calls")
        if calls_page and hasattr(calls_page, "add_call"):
            calls_page.add_call(normalized_event, number, contact_name)

    def _on_notif_changed(self, notif_id):
        log.info("Signal notification changed id=%s", notif_id)
        state.set("notifications", [])
        messages_page = self.get_page("messages")
        if messages_page and hasattr(messages_page, "refresh"):
            QTimer.singleShot(250, messages_page.refresh)

    def _on_battery_updated(self, charge, is_charging):
        dash = self.get_page("dashboard")
        if dash and hasattr(dash, "update_battery"):
            dash.update_battery(charge, is_charging)

    def _on_clipboard_received(self, text):
        log.info("Signal clipboardReceived length=%s", len(text or ""))
        if settings.get("clipboard_autoshare", True):
            QApplication.clipboard().setText(text)
        self._push_clipboard_history(text, source="phone")
        state.set("clipboard_text", text)
        self._last_clipboard_text = text

    def _on_local_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text() or ""
        except Exception:
            text = ""
        if text == self._last_clipboard_text:
            return
        self._last_clipboard_text = text
        if text:
            state.set("clipboard_text", text)
            self._push_clipboard_history(text, source="pc")

    def _auto_connect_bluetooth(self):
        if not settings.get("auto_bt_connect", True):
            return

        def _job():
            try:
                from backend.bluetooth_manager import BluetoothManager
                from backend.settings_store import get as sget
                from backend.ui_feedback import push_toast
                mgr = BluetoothManager()
                if not mgr.available():
                    return
                name_hints = [
                    sget("device_name", ""),
                    "nothing",
                    "phone",
                    "a059",
                ]
                ok, msg = mgr.auto_connect_phone(name_hints)
                if ok:
                    log.info("Bluetooth auto-connect: %s", msg)
                    push_toast("Bluetooth connected to phone", "success", 1800)
                else:
                    log.info("Bluetooth auto-connect skipped/failed: %s", msg)
                    push_toast(f"Bluetooth: {msg}", "warning", 2200)
            except Exception:
                log.exception("Bluetooth auto-connect failed")

        threading.Thread(target=_job, daemon=True).start()

    def _push_clipboard_history(self, text, source="phone"):
        value = (text or "").strip()
        if not value:
            return
        import time
        last = self._clipboard_history[-1] if self._clipboard_history else {}
        if (last.get("text") or "") == value:
            return
        self._clipboard_history.append({
            "text": value,
            "ts": int(time.time()),
            "source": source,
        })
        self._clipboard_history = sanitize_clipboard_history(self._clipboard_history, limit=200)
        settings.set("clipboard_history", self._clipboard_history)
        state.set("clipboard_history", self._clipboard_history)

    def _build_toast_layer(self):
        self._toast_frame = QFrame(self)
        self._toast_frame.setStyleSheet("""
            QFrame {
                background: rgba(12,19,32,230);
                border: 1px solid rgba(255,255,255,0.16);
                border-radius: 12px;
            }
        """)
        self._toast_frame.hide()
        tl = QVBoxLayout(self._toast_frame)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(4)
        self._toast_title = QLabel("")
        self._toast_title.setStyleSheet("color:white;font-size:12px;font-weight:600;background:transparent;border:none;")
        self._toast_msg = QLabel("")
        self._toast_msg.setWordWrap(True)
        self._toast_msg.setStyleSheet("color:rgba(255,255,255,0.75);font-size:11px;background:transparent;border:none;")
        tl.addWidget(self._toast_title)
        tl.addWidget(self._toast_msg)
        self._toast_hide_timer = QTimer(self)
        self._toast_hide_timer.setSingleShot(True)
        self._toast_hide_timer.timeout.connect(self._toast_frame.hide)

    def _on_toast_queue(self, queue):
        rows = list(queue or [])
        if not rows:
            return
        latest = rows[-1]
        level = latest.get("level", "info")
        accent = {
            "success": TEAL,
            "warning": "#D9B36F",
            "error": "#E57A95",
            "info": "#7BBFE8",
        }.get(level, TEAL)
        self._toast_frame.setStyleSheet(f"""
            QFrame {{
                background: rgba(12,19,32,236);
                border: 1px solid {accent}66;
                border-radius: 12px;
            }}
        """)
        title = {
            "success": "Success",
            "warning": "Notice",
            "error": "Error",
            "info": "Info",
        }.get(level, "Info")
        self._toast_title.setText(title)
        self._toast_title.setStyleSheet(f"color:{accent};font-size:12px;font-weight:600;background:transparent;border:none;")
        self._toast_msg.setText(str(latest.get("text") or ""))
        self._toast_frame.adjustSize()
        self._position_toast()
        self._toast_frame.show()
        fade_in(self._toast_frame, level=self._current_motion)
        self._toast_hide_timer.start(int(latest.get("ttl_ms", 2200)))

    def _position_toast(self):
        if not hasattr(self, "_toast_frame"):
            return
        margin = 18
        w = self._toast_frame.width()
        h = self._toast_frame.height()
        self._toast_frame.move(self.width() - w - margin, self.height() - h - margin)

    def apply_visual_settings(self, opacity_pct: int | None = None, motion_level: str | None = None, theme_name: str | None = None):
        theme_changed = False
        if opacity_pct is not None:
            self._current_opacity_pct = int(opacity_pct)
        if motion_level is not None:
            self._current_motion = motion_level
            from ui.theme import set_motion_level
            set_motion_level(self._current_motion)
        if theme_name is not None:
            requested = str(theme_name)
            theme_changed = requested != self._current_theme
            self._current_theme = requested
            set_theme_name(self._current_theme)
            if theme_changed:
                self._rebuild_pages_for_theme()
        set_surface_alpha(self._current_opacity_pct)
        self.setStyleSheet(get_app_style(self._current_opacity_pct))
        if hasattr(self, "_sidebar"):
            self._sidebar.setStyleSheet(theme.SIDEBAR_STYLE)
        self.setWindowOpacity(max(0.72, min(1.0, self._current_opacity_pct / 100.0)))
        refresh_card_styles(self)
        self._position_toast()

    def _poll(self):
        """Refresh current page's data"""
        if not self.isVisible():
            return
        if time.time() < self._suspend_poll_until:
            return
        try:
            current = self._stack.currentWidget()
            if hasattr(current, 'widget'):
                page = current.widget()
                if hasattr(page, 'refresh'):
                    page.refresh()
        except Exception:
            log.exception("Page refresh failed")

    @staticmethod
    def _normalize_call_event(event):
        e = (event or "").strip().lower().replace("-", "_")
        if "missed" in e:
            return "missed_call"
        if e in {"ringing", "callreceived", "incoming", "incoming_call"}:
            return "ringing"
        if e in {"talking", "answered", "in_call", "ongoing", "active", "callstarted"}:
            return "talking"
        return e or "ringing"

    def closeEvent(self, event):
        if (not self._force_quit) and settings.get("close_to_tray", True):
            self.hide()
            event.ignore()
            return
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if hasattr(self, "_bridge"):
            self._bridge.stop()
        QApplication.instance().quit()
        event.accept()

    def hideEvent(self, event):
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._poll_timer.isActive():
            self._poll_timer.start(8000)
        self._position_toast()
        super().showEvent(event)

    def resizeEvent(self, event):
        self._position_toast()
        super().resizeEvent(event)

    def quit_app(self):
        self._force_quit = True
        self.close()
