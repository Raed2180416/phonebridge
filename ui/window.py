"""PhoneBridge main window"""
import logging
import os
import re
import threading
import time

from PyQt6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout,
                              QVBoxLayout, QPushButton, QStackedWidget,
                              QLabel, QScrollArea, QApplication, QFrame)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
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
from ui.runtime_controllers import (
    CallController,
    ClipboardController,
    ConnectivityController,
    HealthController,
    NotificationController,
)
from ui.window_runtime import WindowRuntimeMixin
from ui.window_support import DBusSignalBridge, SidebarIconButton
from backend.state import state
import backend.settings_store as settings
from backend import audio_route
from backend.call_routing import (
    build_call_route_ui_state,
    finalize_pending_call_session,
    normalize_call_event,
    outbound_origin_active,
    phone_match_key,
    notification_reason_can_synthesize,
    allow_call_hint_when_recent_idle,
    plan_polled_call_state,
    reduce_call_session,
    resolve_call_display_name,
    seed_outbound_call_session,
)
from backend.syncthing import Syncthing
from backend.notification_mirror import sync_desktop_notifications
from backend.notifications_state import normalize_notifications
from backend.adb_bridge import ADBBridge

log = logging.getLogger(__name__)

# Pages
from ui.pages.dashboard  import DashboardPage
from ui.pages.messages   import MessagesPage
from ui.pages.calls      import CallsPage
from ui.pages.files      import FilesPage
from ui.pages.mirror     import MirrorPage
from ui.pages.network    import NetworkPage
from ui.pages.settings   import SettingsPage

PAGES = [
    ("⌂",  "Dashboard",  DashboardPage,  "dashboard"),
    ("✉",  "Messages",   MessagesPage,   "messages"),
    ("☎",  "Calls",      CallsPage,      "calls"),
    ("▤",  "Files",      FilesPage,      "files"),
    ("▣",  "Mirror",     MirrorPage,     "mirror"),
    ("◈",  "Network",    NetworkPage,    "network"),
    ("⚙",  "Settings",   SettingsPage,   "settings"),
]

# Separator before Network
SEPARATOR_BEFORE = 5


class PhoneBridgeWindow(WindowRuntimeMixin, QMainWindow):
    # Emitted from the ADB poll background thread to deliver call state
    # to the Qt main thread.  QTimer.singleShot(0,...) from a non-Qt
    # thread does NOT reliably cross to the main event loop in PyQt6.
    _call_state_ready = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PhoneBridge")
        self.setMinimumSize(960, 640)
        self.resize(1140, 740)
        self._current_opacity_pct = int(settings.get("window_opacity", 94) or 94)
        self._current_motion = str(settings.get("motion_level", "subtle") or "subtle")
        self._current_theme = "slate"
        from ui.theme import set_motion_level
        set_motion_level(self._current_motion)
        set_theme_name(self._current_theme)
        set_surface_alpha(self._current_opacity_pct)
        self.setStyleSheet(get_app_style(self._current_opacity_pct))
        opacity = int(settings.get("window_opacity", 94) or 94)
        self.setWindowOpacity(max(0.72, min(1.0, opacity / 100.0)))

        self._page_map = {}
        self._call_popup = None
        self._poll_popup_fallback_timer = QTimer(self)
        self._poll_popup_fallback_timer.setSingleShot(True)
        self._poll_popup_fallback_timer.timeout.connect(self._fire_pending_poll_popup_fallback)
        self._last_call_key = ""
        self._last_call_at = 0.0
        self._last_notif_call_hint_at = 0.0
        self._last_terminal_call_fingerprint = ""
        self._last_terminal_notification_id = ""
        self._last_terminal_notification_updated_at = 0
        self._terminal_idle_boundary_open = True
        self._awaiting_terminal_idle_boundary = False
        self._polled_live_candidate_state = ""
        self._polled_live_candidate_hits = 0
        self._polled_live_candidate_first_at = 0.0
        self._polled_live_candidate_last_at = 0.0
        self._pending_terminal_recent_calls = []
        self._pending_terminal_recent_calls_token = 0
        self._call_contacts_cache_loading = False
        self._suspend_poll_until = 0.0
        self._pending_poll_popup = None
        self._force_quit = False
        self._bridge = None
        self._adb = ADBBridge()
        self._last_polled_call_state = "unknown"
        self._last_polled_at = 0.0
        self._last_non_unknown_polled_call_state = "unknown"
        self._last_non_unknown_polled_at = 0.0
        self._call_state_poll_busy = False
        self._call_state_route_suspended = False
        self._call_session_state = None
        self._call_terminal_timer = QTimer(self)
        self._call_terminal_timer.setSingleShot(True)
        self._call_terminal_timer.timeout.connect(self._finalize_pending_call_terminal)
        # Wire cross-thread delivery: background ADB poll → Qt main thread
        self._call_state_ready.connect(self._apply_polled_call_state)
        self._audio_route_sync_busy = False
        self._mobile_data_policy_busy = False
        self._notif_sync_busy = False
        self._build_ui()
        self._apply_global_audio_route_startup()
        self._build_toast_layer()
        self._center_on_screen()
        # Apply KDE native notification suppression before starting signal
        # listeners to avoid first-notification duplication races at startup.
        self._enforce_notification_popup_policy()
        self._start_signal_bridge()
        if settings.get("auto_bt_connect", True):
            QTimer.singleShot(1800, self._auto_connect_bluetooth)
        self._clipboard_controller = ClipboardController(self)
        self._clipboard_controller.start()

        # Pre-register Hyprland call-popup windowrules via IPC.  The static
        # rules (float, pin, move) only apply at window-creation time, so they
        # must be registered BEFORE the popup is ever shown.  A deferred 0ms
        # shot lets the Wayland compositor finish its own startup handshake first.
        QTimer.singleShot(0, self._register_hyprland_popup_rules)
        # Pre-create the call popup widget so that CallPopup.__init__ + _build_ui()
        # never run during an incoming call.  A 500 ms delay lets the main window
        # finish rendering before we do the extra Qt work.
        QTimer.singleShot(500, self._ensure_call_popup)
        QTimer.singleShot(900, self._warm_call_popup_surface)
        QTimer.singleShot(1200, self._prime_call_contacts_cache_async)

        self._poll_timer = QTimer()
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(8000)
        state.subscribe("ui_toast_queue", self._on_toast_queue, owner=self)
        state.subscribe("syncthing_runtime_status", self._on_syncthing_runtime_status, owner=self)
        state.subscribe("notif_open_request", self._on_notif_open_request, owner=self)
        state.subscribe("call_route_status", lambda _v: self._sync_call_route_ui_state_from_state(), owner=self)
        state.subscribe("call_audio_active", lambda _v: self._sync_call_route_ui_state_from_state(), owner=self)
        state.subscribe("call_route_reason", lambda _v: self._sync_call_route_ui_state_from_state(), owner=self)
        state.subscribe("call_route_backend", lambda _v: self._sync_call_route_ui_state_from_state(), owner=self)
        state.subscribe("call_muted", lambda _v: self._sync_call_route_ui_state_from_state(), owner=self)
        self._sync_call_route_ui_state_from_state()

        self._connectivity_controller = ConnectivityController(self, self._mobile_data_policy_tick)
        self._connectivity_controller.start()

        self._health_controller = HealthController(self, self._kde_health_tick, self._service_health_tick)
        self._health_controller.start()

        self._call_controller = CallController(self, self._poll_phone_call_state_async)
        self._call_controller.start(visible=self.isVisible())

        self._notification_controller = NotificationController(
            self,
            self._sync_notification_mirror_snapshot,
            self._enforce_notification_popup_policy,
        )
        self._notification_controller.prime_startup()

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
            log.info("Window go_to page=%s", page_id)
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

    def show_and_raise(self, *, reason: str = "unspecified"):
        if not self._poll_timer.isActive():
            self._poll_timer.start(8000)
        self._move_to_current_workspace()
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        current_page = ""
        try:
            current_page = str(getattr(self, "_current_page_id", "") or "")
        except Exception:
            current_page = ""
        log.info(
            "Window show_and_raise reason=%s visible=%s active=%s page=%s",
            str(reason or "unspecified"),
            self.isVisible(),
            self.isActiveWindow(),
            current_page,
        )

    def _move_to_current_workspace(self):
        """Best-effort Hyprland move so toggle opens on the active workspace."""
        from backend import hyprland

        hyprland.move_pid_to_active_workspace(os.getpid())

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

    def _on_battery_updated(self, charge, is_charging):
        dash = self.get_page("dashboard")
        if dash and hasattr(dash, "update_battery"):
            dash.update_battery(charge, is_charging)

    def _on_clipboard_received(self, text):
        self._clipboard_controller.apply_remote_text(text)

    @staticmethod
    def _looks_like_mobile_network(name: str) -> bool:
        text = (name or "").strip().lower()
        if not text or text in {"unknown", "none"}:
            return False
        if any(k in text for k in ("wifi", "wlan", "ethernet", "lan")):
            return False
        return bool(re.search(r"(mobile|cell|lte|5g|4g|3g|2g|nr|hspa|edge|gprs)", text))

    def _kde_health_tick(self):
        """Background KDE reachability probe — writes state['kde_health'] without blocking UI."""
        if not settings.get("kde_integration_enabled", True):
            return

        def _job():
            try:
                from backend.kdeconnect import kde_health_probe
                result = kde_health_probe()
                state.set("kde_health", result)
                if result.get("status") == "degraded":
                    log.warning(
                        "KDE health: degraded (reachable=%s, refresh_ok=%s)",
                        result.get("reachable"),
                        result.get("refresh_ok"),
                    )
                elif result.get("status") == "ok":
                    log.debug("KDE health: ok")
            except Exception:
                log.exception("KDE health probe failed")

        threading.Thread(target=_job, daemon=True, name="pb-kde-health").start()

    def _service_health_tick(self):
        """Spawn a background probe of all services; writes state['service_health']."""
        from backend.health import schedule_probe
        schedule_probe()

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
                        net_type = str(self._adb.get_active_network_hint() or "")
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
            state.update(
                "connectivity_status",
                lambda current: {
                    **current,
                    "sync_mobile_policy": {
                        "actual": False,
                        "reachable": True,
                        "reason": f"Paused {len(auto_paused)} folders on mobile data",
                    },
                },
                default={},
            )

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
            state.update(
                "connectivity_status",
                lambda current: {
                    **current,
                    "sync_mobile_policy": {
                        "actual": True,
                        "reachable": True,
                        "reason": "Resumed folders after leaving mobile data",
                    },
                },
                default={},
            )

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
            requested = "slate"
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
        call_ui = state.get("call_ui_state", {}) or {}
        if str(call_ui.get("phase") or call_ui.get("status") or "").strip().lower() in {"ringing", "talking"}:
            return
        try:
            current = self._stack.currentWidget()
            if hasattr(current, 'widget'):
                page = current.widget()
                if hasattr(page, 'refresh') and bool(getattr(page, "allow_periodic_refresh", True)):
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
                self._call_popup.close()
            except Exception:
                pass
        audio_route.set_source("call_pc_active", False)
        audio_route.stop()
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if hasattr(self, "_connectivity_controller") and self._connectivity_controller is not None:
            self._connectivity_controller.stop()
        if hasattr(self, "_health_controller") and self._health_controller is not None:
            self._health_controller.stop()
        if hasattr(self, "_call_controller") and self._call_controller is not None:
            self._call_controller.stop()
        if hasattr(self, "_clipboard_controller") and self._clipboard_controller is not None:
            self._clipboard_controller.stop()
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None
        QApplication.instance().quit()
        event.accept()

    def hideEvent(self, event):
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if hasattr(self, "_call_controller") and self._call_controller is not None:
            self._call_controller.set_window_visible(False)
        if hasattr(self, "_health_controller") and self._health_controller is not None:
            self._health_controller.suspend()
        super().hideEvent(event)

    def showEvent(self, event):
        if not self._poll_timer.isActive():
            self._poll_timer.start(8000)
        if hasattr(self, "_call_controller") and self._call_controller is not None:
            self._call_controller.set_window_visible(True)
        if hasattr(self, "_health_controller") and self._health_controller is not None:
            self._health_controller.resume()
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
