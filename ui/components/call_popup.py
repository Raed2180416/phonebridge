"""Floating call popup with explicit state machine and BT route flow."""

from __future__ import annotations

import logging
import time
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from backend import audio_route
from backend import hyprland
from backend.state import state
from ui.components.call_popup_route import (
    BTRouteWorker,
    BtStepRow,
    COLOR_BG,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_DIM,
    COLOR_GREEN,
    COLOR_ORANGE,
    COLOR_RED,
    COLOR_TEXT,
    COLOR_VIOLET,
    COLOR_YELLOW,
    RouteOption,
)
from ui.components.call_popup_session import CallPopupSessionMixin

log = logging.getLogger(__name__)


class CallPopup(CallPopupSessionMixin, QWidget):
    """Singleton floating call popup that is reused across call events."""

    # Delivers ADB poll results to the main thread safely.
    # QTimer.singleShot(0,...) from a non-Qt thread is silently dropped
    # in this bwrap/Wayland environment; pyqtSignal is the only safe path.
    _poll_state_ready = pyqtSignal(str)
    _popup_action_ready = pyqtSignal(object)

    def __init__(self, parent_window: QWidget | None = None):
        # Pass parent to QWidget so Wayland creates a transient child surface
        # that Hyprland will auto-float instead of tiling.
        super().__init__(parent_window)
        self.parent_window = parent_window
        self._active_popup_width = 300

        self.current_state = "ended"
        self.current_number = ""
        self.current_contact = ""
        self._is_muted = bool(state.get("call_muted", False))
        self._routed_to_pc = False
        self._route_busy = False
        self._allow_close = False
        self._popup_active = False
        self._surface_warmed = False
        self._parked_surface_mode = False

        self._last_event_key = ""
        self._last_event_ts = 0.0
        self._ringing_started_at = 0.0
        self._popup_action_busy = False
        self._popup_action_token_counter = 0
        self._active_popup_action_token = 0
        self._call_session_token = 0
        self._auto_route_applied = False
        self._is_outbound_call = False
        self._route_token_counter = 0
        self._active_route_token: tuple[int, int] | None = None
        self._route_watchdog_token: tuple[int, int] | None = None

        self._call_seconds = 0
        self._call_timer = QTimer(self)
        self._call_timer.setInterval(1000)
        self._call_timer.timeout.connect(self._tick)

        self._state_watch_timer = QTimer(self)
        self._state_watch_timer.setInterval(650)
        self._state_watch_timer.timeout.connect(self._poll_call_state)

        self._route_watchdog = QTimer(self)
        self._route_watchdog.setSingleShot(True)
        self._route_watchdog.timeout.connect(self._on_route_watchdog_timeout)

        self._ring_pulse_anim: QPropertyAnimation | None = None
        self._talk_pulse_anim: QPropertyAnimation | None = None
        self._bt_panel_anim: QPropertyAnimation | None = None

        self._route_worker: BTRouteWorker | None = None

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("PhoneBridge Call")

        self.setObjectName("CallPopup")
        self.setFixedWidth(self._active_popup_width)
        self._hypr_rules_installed = False

        self._build_ui()
        self._set_close_gate(False)
        self._set_extra_buttons(show_reply=False)
        state.subscribe("call_route_ui_state", self._on_call_route_ui_state_changed, owner=self)
        state.subscribe("call_muted", self._on_call_muted_changed, owner=self)
        self._on_call_route_ui_state_changed(state.get("call_route_ui_state", {}))
        self._on_call_muted_changed(state.get("call_muted", False))
        self._poll_state_ready.connect(self._apply_polled_call_state)
        self._popup_action_ready.connect(self._on_popup_action_completed)
        # NOTE: QWidget starts hidden by default; do NOT call self.hide()
        # here.  On Wayland, calling hide() on a never-shown widget can
        # destroy the nascent platform window and prevent show() from
        # recreating it properly.

    def is_popup_active(self) -> bool:
        return bool(self._popup_active)

    def update_position(self):
        # On Wayland move() is ignored; use hyprctl dispatch movewindow instead.
        self._hyprland_reposition()

    def set_parent_window(self, parent_window: QWidget | None):
        self.parent_window = parent_window

    def _install_hyprland_rules(self):
        """Inject Hyprland windowrules via IPC socket as safety net."""
        if self._hypr_rules_installed:
            return
        try:
            hyprland.ensure_call_popup_rules()
            self._hypr_rules_installed = True
        except Exception:
            pass

    def _hyprland_reposition(self):
        """Move popup to top-right via Hyprland IPC socket (works inside bwrap)."""
        if not self.isVisible():
            return
        try:
            app = QApplication.instance()
            screen = app.primaryScreen() if app else None
            if screen:
                geom = screen.availableGeometry()
                x = geom.x() + geom.width() - self.width() - 18
                y = geom.y() + 54
            else:
                x, y = 1600, 54

            if not hyprland.move_window_exact(hyprland.CALL_POPUP_SELECTOR, x, y):
                self._qt_move_fallback()
        except Exception:
            self._qt_move_fallback()

    def _qt_move_fallback(self):
        """Fallback positioning using Qt (may be no-op on Wayland)."""
        app = QApplication.instance()
        if app:
            screen = app.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                self.move(
                    geom.x() + geom.width() - self.width() - 18,
                    geom.y() + 54,
                )

    def _hyprland_focus(self):
        """Bring popup to top of z-stack via Hyprland IPC without stealing keyboard focus."""
        try:
            hyprland.alterzorder_top(hyprland.CALL_POPUP_SELECTOR)
        except Exception:
            pass

    def _hyprland_force_float_and_raise(self):
        """Runtime dispatch: setfloating + pin + alterzorder top.

        Called 200 ms and 600 ms after the popup surface is mapped.  This is
        belt-and-suspenders insurance in case the windowrule fires late or was
        not applied (e.g. first show after a daemon restart).  On Hyprland
        v0.54 the dispatchers ``setfloating`` and ``pin`` are valid for
        existing windows.
        """
        if not self.isVisible():
            return
        try:
            hyprland.set_floating_pinned_top(hyprland.CALL_POPUP_SELECTOR)
        except Exception:
            pass

    def _hyprland_capture_active_window(self) -> str | None:
        """Capture currently-focused Hyprland window selector for later focus restore."""
        try:
            return hyprland.capture_active_window_selector(
                exclude_titles={"PhoneBridge Call"},
                exclude_classes={"phonebridge"},
            )
        except Exception:
            return None

    def _hyprland_restore_focus(self, selector: str | None):
        """Restore keyboard focus to a previously active window."""
        if not selector:
            return
        try:
            hyprland.focus_window(selector)
        except Exception:
            pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("CallPopupCard")
        root.addWidget(self.card)

        body = QVBoxLayout(self.card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.top_bar = QWidget()
        self.top_bar.setFixedHeight(3)
        body.addWidget(self.top_bar)

        header = QHBoxLayout()
        header.setContentsMargins(12, 12, 12, 0)
        header.setSpacing(8)

        self.state_label = QLabel("CALL")
        self.state_label.setStyleSheet(
            "font-family:monospace;font-size:9px;font-weight:600;background:transparent;border:none;"
        )

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(18, 18)
        self.close_btn.setToolTip("Cannot close during active call")
        self.close_btn.setStyleSheet(
            f"background:#212636;color:{COLOR_DIM};border:none;border-radius:9px;font-size:10px;"
        )
        self.close_btn.clicked.connect(self.try_close)

        header.addWidget(self.state_label)
        header.addStretch(1)
        header.addWidget(self.close_btn)
        body.addLayout(header)

        contact_row = QHBoxLayout()
        contact_row.setContentsMargins(12, 0, 12, 12)
        contact_row.setSpacing(10)

        self.avatar = QLabel("?")
        self.avatar.setFixedSize(42, 42)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setStyleSheet(
            """
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #4f8ef7,stop:1 #9b6dff);
            color:white;
            border:none;
            border-radius:21px;
            font-size:17px;
            font-weight:700;
            """
        )

        info = QVBoxLayout()
        info.setSpacing(1)
        self.name_label = QLabel("Unknown")
        self.name_label.setStyleSheet(f"color:{COLOR_TEXT};font-size:15px;font-weight:600;border:none;background:transparent;")
        self.number_label = QLabel("")
        self.number_label.setStyleSheet(f"color:{COLOR_DIM};font-size:10px;font-family:monospace;border:none;background:transparent;")
        self.timer_label = QLabel("00:00")
        self.timer_label.setStyleSheet(f"color:{COLOR_GREEN};font-size:10px;font-family:monospace;border:none;background:transparent;")
        self.timer_label.hide()
        info.addWidget(self.name_label)
        info.addWidget(self.number_label)
        info.addWidget(self.timer_label)

        contact_row.addWidget(self.avatar)
        contact_row.addLayout(info, 1)
        body.addLayout(contact_row)

        self.route_panel = QFrame()
        self.route_panel.setStyleSheet(
            f"QFrame {{background:#1a1e28;border:1px solid {COLOR_BORDER};border-radius:8px;}}"
        )
        route_layout = QVBoxLayout(self.route_panel)
        route_layout.setContentsMargins(11, 9, 11, 9)
        route_layout.setSpacing(6)

        route_title = QLabel("AUDIO ROUTE")
        route_title.setStyleSheet(f"color:{COLOR_DIM};font-size:9px;font-family:monospace;border:none;background:transparent;")
        route_layout.addWidget(route_title)

        route_row = QHBoxLayout()
        route_row.setSpacing(5)

        self.phone_option = RouteOption("📱", "Phone")
        self.phone_option.clicked.connect(self.set_route_phone)
        self.laptop_option = RouteOption("🔊", "Laptop", "BT req.")
        self.laptop_option.clicked.connect(self.set_route_laptop)

        route_row.addWidget(self.phone_option, 1)
        route_row.addWidget(self.laptop_option, 1)
        route_layout.addLayout(route_row)

        self.route_summary_label = QLabel("Speaker: Phone · Mic: Phone")
        self.route_summary_label.setStyleSheet(
            f"color:{COLOR_TEXT};font-size:10px;border:none;background:transparent;"
        )
        route_layout.addWidget(self.route_summary_label)

        self.route_reason_label = QLabel("")
        self.route_reason_label.setWordWrap(True)
        self.route_reason_label.setStyleSheet(
            f"color:{COLOR_DIM};font-size:9px;border:none;background:transparent;"
        )
        route_layout.addWidget(self.route_reason_label)

        body.addWidget(self.route_panel)

        self.bt_panel = QFrame()
        self.bt_panel.setStyleSheet(
            f"QFrame {{background:#1a1e28;border:1px solid {COLOR_BORDER};border-radius:8px;}}"
        )
        self.bt_panel.setMaximumHeight(0)
        bt_layout = QVBoxLayout(self.bt_panel)
        bt_layout.setContentsMargins(0, 2, 0, 2)
        bt_layout.setSpacing(0)

        self.bt_rows = [
            BtStepRow("Checking BT connection", "bluetoothctl info ..."),
            BtStepRow("Checking HFP/HSP profile", "wpctl inspect ..."),
            BtStepRow("Detecting mic input node", "pw-dump | grep bluez ..."),
        ]
        for row in self.bt_rows:
            bt_layout.addWidget(row)

        body.addWidget(self.bt_panel)

        self.primary_row = QHBoxLayout()
        self.primary_row.setContentsMargins(12, 0, 12, 12)
        self.primary_row.setSpacing(5)

        self.primary_btn = QPushButton()
        self.primary_btn.setFixedHeight(38)
        self.secondary_btn = QPushButton()
        self.secondary_btn.setFixedHeight(38)

        self.primary_row.addWidget(self.primary_btn, 1)
        self.primary_row.addWidget(self.secondary_btn, 1)
        body.addLayout(self.primary_row)

        self.extra_row = QHBoxLayout()
        self.extra_row.setContentsMargins(12, 0, 12, 12)
        self.extra_row.setSpacing(5)

        self.reply_btn = QPushButton("Reply SMS")
        self.reply_btn.setFixedHeight(28)
        self.reply_btn.clicked.connect(self.sms_reply_diversion_flow)

        self.extra_row.addWidget(self.reply_btn, 1)
        body.addLayout(self.extra_row)

        self.setStyleSheet(
            f"""
            QWidget#CallPopupCard {{
                background-color: {COLOR_BG};
                border: 1px solid {COLOR_BORDER};
                border-radius: 12px;
            }}
            """
        )

    def _button_style(self, role: str, *, active: bool = False) -> str:
        if role == "answer":
            return "background:#22c55e;color:white;border:none;border-radius:8px;font-size:12px;font-weight:500;"
        if role == "reject":
            return "background:#f05252;color:white;border:none;border-radius:8px;font-size:12px;font-weight:500;"
        if role == "neutral":
            return (
                f"background:#212636;color:#94a3b8;border:1px solid {COLOR_BORDER};"
                "border-radius:8px;font-size:12px;font-weight:500;"
            )
        if role == "mute":
            if active:
                return (
                    "background:rgba(240,82,82,0.10);color:#f05252;"
                    "border:1px solid rgba(240,82,82,0.30);border-radius:8px;font-size:12px;font-weight:500;"
                )
            return (
                f"background:#212636;color:#94a3b8;border:1px solid {COLOR_BORDER};"
                "border-radius:8px;font-size:12px;font-weight:500;"
            )
        return f"background:#212636;color:{COLOR_TEXT};border:1px solid {COLOR_BORDER};border-radius:8px;"

    def _extra_button_style(self) -> str:
        return (
            f"background:#1a1e28;color:{COLOR_DIM};border:1px solid {COLOR_BORDER};"
            "border-radius:5px;font-size:10px;font-family:monospace;"
        )

    def _set_close_gate(self, enabled: bool):
        self.close_btn.setEnabled(bool(enabled))
        effect = self.close_btn.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.close_btn)
            self.close_btn.setGraphicsEffect(effect)
        effect.setOpacity(1.0 if enabled else 0.25)

    def _start_ringing_pulse(self):
        self._stop_label_pulse()
        effect = self.top_bar.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.top_bar)
            self.top_bar.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(1000)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.45)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        anim.start()
        self._ring_pulse_anim = anim

    def _stop_ringing_pulse(self):
        if self._ring_pulse_anim is not None:
            self._ring_pulse_anim.stop()
            self._ring_pulse_anim = None
        self.top_bar.setGraphicsEffect(None)

    def _start_label_pulse(self):
        effect = self.state_label.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self.state_label)
            self.state_label.setGraphicsEffect(effect)
        effect.setOpacity(1.0)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(1200)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.4)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)
        anim.start()
        self._talk_pulse_anim = anim

    def _stop_label_pulse(self):
        if self._talk_pulse_anim is not None:
            self._talk_pulse_anim.stop()
            self._talk_pulse_anim = None
        self.state_label.setGraphicsEffect(None)

    def _set_top_bar(self, state_name: str):
        if state_name == "ringing":
            style = (
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #f97316, stop:1 #f0b429);"
            )
            label = "↑ CALLING..." if getattr(self, "_is_outbound_call", False) else "↓ INCOMING CALL"
            color = COLOR_ORANGE
        elif state_name == "talking":
            style = (
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "stop:0 #22c55e, stop:1 #4f8ef7);"
            )
            label = "● TALKING"
            color = COLOR_GREEN
        elif state_name == "missed_call":
            style = f"background:{COLOR_RED};"
            label = "↙ MISSED CALL"
            color = COLOR_RED
        else:
            style = "background:#4e5a72;"
            label = "CALL ENDED"
            color = COLOR_DIM

        self.top_bar.setStyleSheet(style)
        self.state_label.setText(label)
        self.state_label.setStyleSheet(
            f"font-family:monospace;font-size:9px;font-weight:600;color:{color};background:transparent;border:none;"
        )

    def _set_route_panel_visible(self, visible: bool):
        self.route_panel.setVisible(bool(visible))
        if not visible:
            self._animate_bt_panel(False)
        self._sync_popup_size()

    def _set_extra_buttons(self, *, show_reply: bool):
        self.reply_btn.setVisible(show_reply)
        self.reply_btn.setStyleSheet(self._extra_button_style())
        self._sync_popup_size()

    def _set_primary_actions(
        self,
        left_text: str,
        left_style: str,
        left_cb: Callable[[], None],
        right_text: str,
        right_style: str,
        right_cb: Callable[[], None],
        *,
        right_checkable: bool = False,
        right_checked: bool = False,
        right_visible: bool = True,
    ):
        for btn in (self.primary_btn, self.secondary_btn):
            try:
                btn.clicked.disconnect()
            except Exception:
                pass

        self.primary_btn.setText(left_text)
        self.primary_btn.setStyleSheet(left_style)
        self.primary_btn.setCheckable(False)
        self.primary_btn.clicked.connect(left_cb)

        self.secondary_btn.setText(right_text)
        self.secondary_btn.setStyleSheet(right_style)
        self.secondary_btn.setCheckable(bool(right_checkable))
        self.secondary_btn.setChecked(bool(right_checked) if right_checkable else False)
        self.secondary_btn.clicked.connect(right_cb)
        self.secondary_btn.setVisible(bool(right_visible))

    def _on_call_route_ui_state_changed(self, _payload):
        self._sync_route_tiles_from_state()
        self._sync_talking_actions()

    def _on_call_muted_changed(self, muted):
        self._is_muted = bool(muted)
        if self.current_state == "talking" and self.secondary_btn.isVisible():
            self.secondary_btn.setChecked(self._is_muted)
            self.secondary_btn.setStyleSheet(self._button_style("mute", active=self._is_muted))

    def _sync_route_tiles_from_state(self):
        route_ui = state.get("call_route_ui_state", {}) or {}
        status = str(route_ui.get("status") or "phone").strip().lower()
        reason = str(route_ui.get("reason") or "").strip()
        speaker_target = str(route_ui.get("speaker_target") or "Phone").strip() or "Phone"
        mic_target = str(route_ui.get("mic_target") or "Phone").strip() or "Phone"

        self._route_busy = status == "pending"
        self._routed_to_pc = status == "laptop"

        self.route_summary_label.setText(f"Speaker: {speaker_target} · Mic: {mic_target}")
        self.route_reason_label.setText(reason)
        self.route_reason_label.setVisible(bool(reason))

        if status == "pending":
            self._set_laptop_pending()
            return
        if self._routed_to_pc:
            self.phone_option.set_mode(selected=False)
            self.laptop_option.set_mode(selected=True, failed=False, subtext="ready")
            return
        if status == "failed":
            subtext = reason or "failed"
            if len(subtext) > 28:
                subtext = subtext[:25] + "..."
            self.phone_option.set_mode(selected=True)
            self.laptop_option.set_mode(selected=False, failed=True, subtext=subtext)
            return
        self._set_phone_selected(reset_failure=False)

    def _sync_talking_actions(self):
        if self.current_state != "talking":
            return
        route_ui = state.get("call_route_ui_state", {}) or {}
        mute_available = bool(route_ui.get("mute_available", False))
        self._set_primary_actions(
            "End",
            self._button_style("reject"),
            self.end_call,
            "Mute",
            self._button_style("mute", active=self._is_muted),
            self.toggle_mute,
            right_checkable=True,
            right_checked=self._is_muted,
            right_visible=mute_available,
        )
        self._sync_popup_size()

    def _sync_popup_size(self):
        if getattr(self, "_parked_surface_mode", False):
            try:
                self.resize(1, 1)
            except Exception:
                pass
            return
        try:
            # Clear previous hard height clamps so the current layout can
            # recalculate a fresh size hint before we clamp again.
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            if self.layout() is not None:
                self.layout().activate()
            if self.card.layout() is not None:
                self.card.layout().activate()
            self.adjustSize()
            target_h = max(120, int(self.sizeHint().height()))
            self.setMinimumHeight(target_h)
            self.setMaximumHeight(target_h)
            self.resize(self.width(), target_h)
        except Exception:
            return

    def _set_parked_surface_mode(self, parked: bool):
        self._parked_surface_mode = bool(parked)
        if parked:
            self.card.setVisible(False)
            self.setMinimumSize(1, 1)
            self.setMaximumSize(1, 1)
            self.resize(1, 1)
            return
        self.card.setVisible(True)
        self.setMinimumWidth(self._active_popup_width)
        self.setMaximumWidth(self._active_popup_width)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.resize(self._active_popup_width, max(120, int(self.sizeHint().height())))

    def _animate_bt_panel(self, show: bool):
        target = 96 if show else 0
        if self._bt_panel_anim is not None:
            self._bt_panel_anim.stop()
        anim = QPropertyAnimation(self.bt_panel, b"maximumHeight", self)
        anim.setDuration(280)
        anim.setStartValue(self.bt_panel.maximumHeight())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda _value: self._sync_popup_size())
        anim.finished.connect(self._sync_popup_size)
        anim.start()
        self._bt_panel_anim = anim

    def _show_popup(self):
        show_started = time.perf_counter()
        if self.isVisible():
            # Keep the popup above other windows without stealing keyboard focus.
            # On Wayland, QWidget may report visible=True while compositor mapping
            # lags; force a show cycle and re-apply compositor placement rules.
            if not self._popup_active:
                try:
                    self._set_parked_surface_mode(False)
                    self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
                    self.setWindowOpacity(1.0)
                    if self.isMinimized():
                        self.showNormal()
                except Exception:
                    log.debug("Failed activating parked popup surface", exc_info=True)
                self._popup_active = True
                self._install_hyprland_rules()
                self._sync_popup_size()
                self._hyprland_force_float_and_raise()
                self._hyprland_reposition()
                log.info(
                    "Call popup activated from parked surface dt_ms=%.1f visible=%s geometry=%s",
                    (time.perf_counter() - show_started) * 1000.0,
                    self.isVisible(),
                    self.geometry(),
                )
                return
            try:
                if self.isMinimized():
                    self.showNormal()
                else:
                    self.show()
            except Exception:
                log.debug("Failed re-raising visible popup", exc_info=True)
            self._install_hyprland_rules()
            self._sync_popup_size()
            self._hyprland_reposition()
            # Schedule a fast backup reposition: the surface may not be mapped
            # yet even though isVisible() is True (Qt marks visible before the
            # Wayland surface commit round-trip completes).  50 ms gives the
            # compositor time to map the window so movewindowpixel succeeds.
            def _post_map_reraised():
                self._hyprland_force_float_and_raise()
                self._hyprland_reposition()
                self._sync_popup_size()
            QTimer.singleShot(50, _post_map_reraised)
            log.info(
                "Call popup re-raised (already visible) dt_ms=%.1f",
                (time.perf_counter() - show_started) * 1000.0,
            )
            return

        # Always detach from parent so the popup gets its own Wayland surface.
        if self.parent() is not None:
            self.setParent(None)
            self.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setWindowTitle("PhoneBridge Call")

        self.setWindowOpacity(1.0)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._set_parked_surface_mode(False)
        self._sync_popup_size()
        self._allow_close = True
        self.show()
        self._allow_close = False
        self._popup_active = True
        self._surface_warmed = True
        # Attempt immediate reposition right after show(). The surface may not
        # be mapped yet (Wayland round-trip pending) so this can fail silently;
        # the 0 ms / 50 ms timer callbacks below are the reliable fallback.
        self._hyprland_reposition()

        log.info(
            "Call popup shown (visible=%s winId=%s geometry=%s dt_ms=%.1f)",
            self.isVisible(),
            int(self.winId()),
            self.geometry(),
            (time.perf_counter() - show_started) * 1000.0,
        )

        # Delayed Hyprland rule injection + reposition after compositor maps.
        # We also runtime-dispatch setfloating/pin/alterzorder to ensure the
        # popup is floating and on top even if windowrules fire late or the
        # window was previously tiled.
        #
        # Timing rationale: calls can last < 1 s (ringing → missed in <100 ms).
        # We need the popup at its final screen position as fast as possible.
        # Static windowrules (float, pin, move) are pre-registered at startup so
        # Hyprland applies them the instant this surface opens. These timers are a
        # safety net for the runtime dispatches (setfloating, alterzorder,
        # movewindowpixel) that can't be done via static rules.
        #
        # 0 ms  – fires after the current event batch; may already be mapped.
        # 50 ms – reliable first shot after a Wayland round-trip.
        # 300 ms – late safety net (covers main-thread blocking during audio/BT
        #          setup that delays earlier timer callbacks).
        def _post_map_fast():
            self._hyprland_force_float_and_raise()
            self._hyprland_reposition()
            self._sync_popup_size()

        def _post_map_full():
            self._install_hyprland_rules()   # re-inject static rules as safety net
            self._hyprland_force_float_and_raise()
            self._hyprland_reposition()
            self._sync_popup_size()

        QTimer.singleShot(0, _post_map_fast)
        QTimer.singleShot(50, _post_map_fast)
        QTimer.singleShot(300, _post_map_full)

    def hide_popup(self):
        if not self.isVisible() and not self._popup_active:
            return
        self._hide_in_place()

    def warmup_surface(self):
        """Prepare popup state without mapping a stray startup surface."""
        if self._surface_warmed:
            return
        try:
            self._install_hyprland_rules()
            self._set_parked_surface_mode(False)
            self._sync_popup_size()
            self._popup_active = False
            self._surface_warmed = True
        except Exception:
            log.debug("Call popup warmup failed", exc_info=True)
            return
        self._animate_bt_panel(False)
        log.info("Call popup warmup prepared hidden surface")

    def _close_popup_now(self):
        if self.isVisible() or self._popup_active:
            self._hide_in_place()

    def _hide_in_place(self):
        self._popup_active = False
        self._allow_close = True
        try:
            self._animate_bt_panel(False)
            self._set_parked_surface_mode(False)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setWindowOpacity(1.0)
            if self.isVisible():
                self.hide()
        finally:
            self._allow_close = False

    def _refresh_contact(self):
        display_name = self.current_contact or self.current_number or "Unknown"
        self.name_label.setText(display_name)
        self.number_label.setText(self.current_number if self.current_number and self.current_number != display_name else "")

        initial = "?"
        for ch in display_name:
            if ch.strip():
                initial = ch.upper()
                break
        self.avatar.setText(initial)

    def _publish_state(self, status: str, audio_target: str | None = None):
        row = dict(state.get("call_ui_state", {}) or {})
        row.update(
            {
                "phase": str(status or row.get("phase") or "ended"),
                "status": str(status or row.get("status") or "ended"),
                "number": self.current_number,
                "display_name": self.current_contact,
                "contact_name": self.current_contact,
                "audio_target": audio_target
                or ("pc" if self._routed_to_pc else ("pending_pc" if self._route_busy else "phone")),
                "updated_at": int(time.time() * 1000),
            }
        )
        state.set_many(
            {
                "call_state": {
                    "event": str(row.get("status") or "ended"),
                    "number": self.current_number,
                    "contact_name": self.current_contact,
                },
                "call_ui_state": row,
            }
        )

    def _begin_call_session(self):
        self._call_session_token += 1
        self._auto_route_applied = False
        self._is_outbound_call = False
        self._invalidate_route_callbacks()
        self._route_busy = False
        self._routed_to_pc = False
        state.set("call_muted", False)
        self._set_phone_selected(reset_failure=True)
        self._animate_bt_panel(False)
        for row in self.bt_rows:
            row.set_state("pending")

    def _invalidate_route_callbacks(self):
        self._active_route_token = None
        self._route_watchdog_token = None
        self._route_watchdog.stop()

    def _route_callback_stale(self, token: tuple[int, int] | None) -> bool:
        if token is None:
            return True
        if token != self._active_route_token:
            return True
        if token[0] != self._call_session_token:
            return True
        if self.current_state not in {"ringing", "talking"}:
            return True
        return False
