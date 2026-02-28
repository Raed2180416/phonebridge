"""PhoneBridge main window"""
try:
    import dbus  # type: ignore
    import dbus.mainloop.glib  # type: ignore
    from gi.repository import GLib  # type: ignore
    _HAVE_DBUS = True
except Exception:
    dbus = None
    GLib = None
    _HAVE_DBUS = False
import logging
import os
import shutil
import subprocess
import time
import threading
import re

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout,
                              QVBoxLayout, QPushButton, QStackedWidget,
                              QLabel, QScrollArea, QApplication, QFrame)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QPoint, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QFont
from ui.theme import (
    TEAL,
    VIOLET,
    BORDER,
    TEXT_DIM,
    with_alpha,
    get_app_style,
    set_surface_alpha,
    refresh_card_styles,
    set_theme_name,
)
import ui.theme as theme
from ui.motion import fade_in
from backend.state import state
import backend.settings_store as settings
from backend import audio_route
from backend.call_routing import normalize_call_event, should_suppress_popup
from backend.clipboard_history import sanitize_clipboard_history
from backend.syncthing import Syncthing
from backend.notification_mirror import sync_desktop_notifications

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
        if not _HAVE_DBUS:
            raise RuntimeError("python-dbus/gi not available; KDE integration disabled")
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
        r = self.rect()

        if self._bg_opacity > 0.0:
            bg = QColor("#1a1e28")
            bg.setAlphaF(self._bg_opacity)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(r, 8, 8)

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
        painter.drawText(r, int(Qt.AlignmentFlag.AlignCenter), self._icon_text)
        painter.end()


class PhoneBridgeWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhoneBridge")
        self.setMinimumSize(960, 640)
        self.resize(1140, 740)
        self._current_opacity_pct = int(settings.get("window_opacity", 94) or 94)
        self._current_motion = str(settings.get("motion_level", "subtle") or "subtle")
        self._current_theme = str(settings.get("theme_name", "slate") or "slate")
        from ui.theme import set_motion_level
        set_motion_level(self._current_motion)
        set_theme_name(self._current_theme)
        set_surface_alpha(self._current_opacity_pct)
        self.setStyleSheet(get_app_style(self._current_opacity_pct))
        opacity = int(settings.get("window_opacity", 94) or 94)
        self.setWindowOpacity(max(0.72, min(1.0, opacity / 100.0)))

        self._page_map = {}
        self._call_popup = None
        self._last_clipboard_text = ""
        self._clipboard_history = []
        self._last_call_key = ""
        self._last_call_at = 0.0
        self._suspend_poll_until = 0.0
        self._force_quit = False
        self._bridge = None
        self._mobile_data_policy_busy = False
        self._notif_sync_busy = False
        self._build_ui()
        self._apply_global_audio_route_startup()
        self._build_toast_layer()
        self._clipboard_history = sanitize_clipboard_history(settings.get("clipboard_history", []) or [])
        settings.set("clipboard_history", self._clipboard_history)
        state.set("clipboard_history", self._clipboard_history)
        self._center_on_screen()
        self._start_signal_bridge()
        if settings.get("auto_bt_connect", True):
            QTimer.singleShot(1800, self._auto_connect_bluetooth)
        clip = QApplication.clipboard()
        clip.dataChanged.connect(self._on_local_clipboard_changed)
        self._on_local_clipboard_changed()

        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(8000)
        state.subscribe("ui_toast_queue", self._on_toast_queue)

        self._policy_timer = QTimer()
        self._policy_timer.timeout.connect(self._mobile_data_policy_tick)
        self._policy_timer.start(15000)
        self._mobile_data_policy_tick()
        QTimer.singleShot(900, self._sync_notification_mirror_snapshot)
        QTimer.singleShot(1200, self._enforce_notification_popup_policy)

    def _apply_global_audio_route_startup(self):
        enabled = bool(settings.get("audio_redirect", False))
        audio_route.set_source("ui_global_toggle", enabled)
        if not audio_route.sync():
            settings.set("audio_redirect", False)
            audio_route.set_source("ui_global_toggle", False)

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
        sidebar.setFixedWidth(56)
        sidebar.setStyleSheet(theme.SIDEBAR_STYLE)
        self._sidebar = sidebar
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(8,12,8,12)
        sb.setSpacing(12)

        self._dashboard_btn = SidebarIconButton("⌂", font_size=17)
        self._dashboard_btn.setObjectName("sb-item")
        self._dashboard_btn.setToolTip("Dashboard")
        self._dashboard_btn.setFixedSize(40, 40)
        self._dashboard_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dashboard_btn.setAutoDefault(False)
        self._dashboard_btn.clicked.connect(lambda _: self.set_page(0))
        sb.addWidget(self._dashboard_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        sb.addSpacing(6)

        sep_widget = QWidget()
        sep_widget.setFixedHeight(1)
        sep_widget.setStyleSheet(f"background:{BORDER};border:none;")
        sb.addWidget(sep_widget)
        sb.addSpacing(6)

        self._sb_buttons = {}
        self._stack = QStackedWidget()

        for i, (icon, name, PageClass, page_id) in enumerate(PAGES):
            if i == SEPARATOR_BEFORE:
                sb.addSpacing(6)
                sep2 = QWidget()
                sep2.setFixedHeight(1)
                sep2.setStyleSheet(f"background:{BORDER};border:none;")
                sb.addWidget(sep2)
                sb.addSpacing(6)

            if page_id != "dashboard":
                btn = SidebarIconButton(icon)
                btn.setObjectName("sb-item")
                btn.setToolTip(name)
                btn.setFixedSize(40, 40)
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
        if hasattr(self, "_dashboard_btn") and self._dashboard_btn is not None:
            self._dashboard_btn.set_active(index == 0)
        for page_index, btn in self._sb_buttons.items():
            btn.set_active(page_index == index)

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
        self.showNormal()
        self.show()
        self._move_to_current_workspace()
        self.raise_()
        self.activateWindow()

    def _move_to_current_workspace(self):
        """Best-effort Hyprland move so toggle opens on the active workspace."""
        if not shutil.which("hyprctl"):
            return
        try:
            subprocess.run(
                ["hyprctl", "dispatch", "movetoworkspacesilent", f"current,pid:{os.getpid()}"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
        except Exception:
            pass

    def run_startup_check(
        self,
        from_tray: bool = False,
        background_mode: bool = False,
        anchor_pos=None,
        close_on_mouse_leave: bool = False,
    ):
        from backend.startup_check import StartupChecker
        StartupChecker(self).run_and_show(
            from_tray=bool(from_tray),
            background_mode=bool(background_mode),
            anchor_pos=anchor_pos,
            close_on_mouse_leave=bool(close_on_mouse_leave),
        )

    def _ensure_call_popup(self):
        if self._call_popup is None:
            from ui.components.call_popup import CallPopup

            self._call_popup = CallPopup(self if self.isVisible() else None)
        self._call_popup.set_parent_window(self if self.isVisible() else None)
        return self._call_popup

    def _update_call_popup_position(self):
        popup = self._call_popup
        if popup is None:
            return
        popup.set_parent_window(self if self.isVisible() else None)
        if popup.isVisible():
            popup.update_position()

    def _publish_call_snapshot(self, status: str, number: str, contact_name: str, audio_target: str = "phone"):
        normalized = normalize_call_event(status)
        state.set(
            "call_state",
            {"event": normalized, "number": number, "contact_name": contact_name},
        )
        state.set(
            "call_ui_state",
            {
                "status": normalized,
                "number": number,
                "contact_name": contact_name,
                "audio_target": audio_target,
                "updated_at": int(time.time() * 1000),
            },
        )

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
        if self._bridge is not None:
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
            self._bridge = None

    def set_kde_integration(self, enabled: bool):
        target = bool(enabled)
        settings.set("kde_integration_enabled", target)
        if target:
            self._start_signal_bridge()
            self._sync_notification_mirror_snapshot()
            return True
        if self._bridge is not None:
            try:
                self._bridge.stop()
            except Exception:
                pass
            self._bridge = None
        sync_desktop_notifications([])
        return True

    def _on_call_received(self, event, number, contact_name):
        normalized_event = normalize_call_event(event)
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
        outbound_origin = state.get("outbound_call_origin", {}) or {}
        outbound_active = should_suppress_popup(normalized_event, outbound_origin, now_ms=int(time.time() * 1000))
        if normalized_event == "ringing" and not outbound_active:
            # Fresh incoming call sessions must not inherit stale laptop route ownership.
            audio_route.set_source("call_pc_active", False)
        if normalized_event == "ended":
            audio_route.set_source("call_pc_active", False)
            mirror_running = False
            mirror = self.get_page("mirror")
            if mirror and hasattr(mirror, "is_mirror_stream_running"):
                try:
                    mirror_running = bool(mirror.is_mirror_stream_running())
                except Exception:
                    mirror_running = False
            audio_route.sync(suspend_ui_global=mirror_running)
            popup = self._call_popup
            if popup is not None:
                try:
                    popup.handle_call_event(number, contact_name, "ended")
                except Exception:
                    pass
            self._publish_call_snapshot("ended", number, contact_name, "phone")
            state.set("outbound_call_origin", {})
            return
        if normalized_event == "missed_call":
            audio_route.set_source("call_pc_active", False)
            mirror_running = False
            mirror = self.get_page("mirror")
            if mirror and hasattr(mirror, "is_mirror_stream_running"):
                try:
                    mirror_running = bool(mirror.is_mirror_stream_running())
                except Exception:
                    mirror_running = False
            audio_route.sync(suspend_ui_global=mirror_running)
        if normalized_event in {"ringing", "talking"}:
            # During active/ringing calls, suppress global media redirect unless
            # user explicitly routes call audio to PC.
            audio_route.sync(suspend_ui_global=True)
        if outbound_active and normalized_event in {"ringing", "talking"}:
            # Calls started from Calls page should not spawn popup.
            self._publish_call_snapshot(
                normalized_event,
                number,
                contact_name,
                "pc" if state.get("call_audio_active", False) else "phone",
            )
            calls_page = self.get_page("calls")
            if calls_page and hasattr(calls_page, "add_call"):
                calls_page.add_call(normalized_event, number, contact_name)
            return
        if settings.get("suppress_calls"):
            self._publish_call_snapshot(
                normalized_event,
                number,
                contact_name,
                "pc" if state.get("call_audio_active", False) else "phone",
            )
            calls_page = self.get_page("calls")
            if calls_page and hasattr(calls_page, "add_call"):
                calls_page.add_call(normalized_event, number, contact_name)
            return
        try:
            popup = self._ensure_call_popup()
            popup.handle_call_event(number, contact_name, normalized_event)
        except Exception:
            log.exception("Failed to render call popup")
            self._publish_call_snapshot(normalized_event, number, contact_name, "phone")

        # Also refresh calls page
        calls_page = self.get_page("calls")
        if calls_page and hasattr(calls_page, "add_call"):
            calls_page.add_call(normalized_event, number, contact_name)

    def _on_notif_changed(self, notif_id):
        log.info("Signal notification changed id=%s", notif_id)
        state.set("notif_revision", {
            "id": str(notif_id),
            "updated_at": int(time.time() * 1000),
        })
        self._sync_notification_mirror_snapshot()
        messages_page = self.get_page("messages")
        if messages_page and hasattr(messages_page, "refresh"):
            QTimer.singleShot(120, messages_page.refresh)

    def _sync_notification_mirror_snapshot(self):
        if self._notif_sync_busy:
            return
        if not settings.get("kde_integration_enabled", True):
            sync_desktop_notifications([])
            return
        self._notif_sync_busy = True

        def _job():
            try:
                from backend.kdeconnect import KDEConnect
                rows = KDEConnect().get_notifications()
                state.set("notifications", rows)
                sync_desktop_notifications(rows)
            except Exception:
                log.exception("Notification mirror snapshot sync failed")
            finally:
                self._notif_sync_busy = False

        threading.Thread(target=_job, daemon=True).start()

    @staticmethod
    def _enforce_notification_popup_policy():
        try:
            from backend.kdeconnect import KDEConnect
            KDEConnect.suppress_native_notification_popups(True)
        except Exception:
            log.exception("Failed applying KDE notification popup policy")

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

    @staticmethod
    def _looks_like_mobile_network(name: str) -> bool:
        text = (name or "").strip().lower()
        if not text or text in {"unknown", "none"}:
            return False
        if any(k in text for k in ("wifi", "wlan", "ethernet", "lan")):
            return False
        return bool(re.search(r"(mobile|cell|lte|5g|4g|3g|2g|nr|hspa|edge|gprs)", text))

    def _mobile_data_policy_tick(self):
        if self._mobile_data_policy_busy:
            return
        self._mobile_data_policy_busy = True

        def _job():
            try:
                from backend.kdeconnect import KDEConnect
                net_type = ""
                if settings.get("kde_integration_enabled", True):
                    try:
                        net_type = str(KDEConnect().get_network_type() or "")
                    except Exception:
                        net_type = ""
                if not net_type or net_type.strip().lower() in {"", "unknown", "none"}:
                    try:
                        net_type = str(self.adb.get_active_network_hint() or "")
                    except Exception:
                        net_type = ""
                self._apply_mobile_data_sync_policy(net_type)
            finally:
                self._mobile_data_policy_busy = False

        threading.Thread(target=_job, daemon=True).start()

    def _apply_mobile_data_sync_policy(self, network_type: str):
        if settings.get("sync_on_mobile_data", False):
            self._resume_auto_paused_sync_folders()
            return
        if not self._looks_like_mobile_network(network_type):
            self._resume_auto_paused_sync_folders()
            return
        st = Syncthing()
        if not st.is_running():
            return
        folders = st.get_folders() or []
        auto_paused = []
        for f in folders:
            fid = str(f.get("id") or "").strip()
            if not fid:
                continue
            if not bool(f.get("paused", False)):
                if st.pause_folder(fid):
                    auto_paused.append(fid)
        if auto_paused:
            state.set("mobile_data_auto_paused", auto_paused)
            state.set("connectivity_status", {
                **(state.get("connectivity_status", {}) or {}),
                "sync_mobile_policy": {
                    "actual": False,
                    "reachable": True,
                    "reason": f"Paused {len(auto_paused)} folders on mobile data",
                },
            })

    def _resume_auto_paused_sync_folders(self):
        paused = list(state.get("mobile_data_auto_paused", []) or [])
        if not paused:
            return
        st = Syncthing()
        if not st.is_running():
            return
        resumed = []
        for fid in paused:
            if st.resume_folder(str(fid)):
                resumed.append(fid)
        if resumed:
            state.set("mobile_data_auto_paused", [x for x in paused if x not in resumed])
            state.set("connectivity_status", {
                **(state.get("connectivity_status", {}) or {}),
                "sync_mobile_policy": {
                    "actual": True,
                    "reachable": True,
                    "reason": "Resumed folders after leaving mobile data",
                },
            })

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
                mgr = BluetoothManager()
                if not mgr.available():
                    return
                name_hints = [
                    sget("device_name", ""),
                    "nothing",
                    "phone",
                    "a059",
                ]
                ok, msg = mgr.auto_connect_phone(
                    name_hints,
                    call_ready_only=bool(sget("bt_call_ready_mode", False)),
                )
                if ok:
                    log.info("Bluetooth auto-connect: %s", msg)
                else:
                    log.info("Bluetooth auto-connect skipped/failed: %s", msg)
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
                border: 1px solid {with_alpha(accent, 0.50)};
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

    # Call event normalization lives in backend/call_routing.py (pure module)

    def closeEvent(self, event):
        if (not self._force_quit) and settings.get("close_to_tray", True):
            audio_route.set_source("call_pc_active", False)
            audio_route.stop()
            self.hide()
            event.ignore()
            return
        if self._call_popup is not None:
            try:
                self._call_popup.hide()
            except Exception:
                pass
        audio_route.set_source("call_pc_active", False)
        audio_route.stop()
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if hasattr(self, "_policy_timer") and self._policy_timer.isActive():
            self._policy_timer.stop()
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None
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
        self._update_call_popup_position()
        super().showEvent(event)

    def resizeEvent(self, event):
        self._position_toast()
        self._update_call_popup_position()
        super().resizeEvent(event)

    def moveEvent(self, event):
        self._update_call_popup_position()
        super().moveEvent(event)

    def quit_app(self):
        self._force_quit = True
        self.close()
