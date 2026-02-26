"""PhoneBridge UI theme and shared components."""
import sys

from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QLineEdit,
    QTextEdit,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QColor

# Theme palettes
_THEMES = {
    "slate": {
        "accent_primary": "#53D1B0",
        "accent_secondary": "#66C4E3",
        "accent_violet": "#A38EF2",
        "accent_blue": "#7EAFFF",
        "accent_success": "#53D1B0",
        "accent_warning": "#D9B36F",
        "danger": "#E57A95",
        "text_primary": "rgba(255,255,255,0.93)",
        "text_secondary": "rgba(255,255,255,0.64)",
        "text_dim": "rgba(255,255,255,0.40)",
        "bg": "#0C1320",
    },
    "mist": {
        "accent_primary": "#5DB6B1",
        "accent_secondary": "#75C1CF",
        "accent_violet": "#A8A9E0",
        "accent_blue": "#8AB7FF",
        "accent_success": "#5DB6B1",
        "accent_warning": "#C7A267",
        "danger": "#CF6E86",
        "text_primary": "rgba(242,246,248,0.95)",
        "text_secondary": "rgba(232,238,242,0.70)",
        "text_dim": "rgba(224,232,238,0.45)",
        "bg": "#122028",
    },
    "night": {
        "accent_primary": "#6FD0B8",
        "accent_secondary": "#82C8EA",
        "accent_violet": "#9EA8FF",
        "accent_blue": "#86B3FF",
        "accent_success": "#6FD0B8",
        "accent_warning": "#D1B47B",
        "danger": "#DE6F8F",
        "text_primary": "rgba(246,248,255,0.96)",
        "text_secondary": "rgba(218,225,242,0.72)",
        "text_dim": "rgba(202,210,230,0.45)",
        "bg": "#0A0E18",
    },
}

CURRENT_THEME = "slate"

# Backward-compatible color names
TEAL = "#53D1B0"
VIOLET = "#9BA3F7"
CYAN = "#7BBFE8"
ROSE = "#E57A95"
AMBER = "#D9B36F"
BLUE = "#84AEFF"
BG = "#0C1320"
BORDER = "rgba(255,255,255,0.10)"
BORDER2 = "rgba(255,255,255,0.16)"
TEXT = "rgba(255,255,255,0.93)"
TEXT_MID = "rgba(255,255,255,0.64)"
TEXT_DIM = "rgba(255,255,255,0.40)"

# Semantic surfaces/states
SURFACE_ALPHA = 0.40
SURFACE_ELEVATED_ALPHA = 0.54
SURFACE_BORDER_ALPHA = 0.11
MOTION_LEVEL = "rich"


def _apply_theme(theme_name: str):
    global CURRENT_THEME, TEAL, VIOLET, CYAN, ROSE, AMBER, BLUE, BG, TEXT, TEXT_MID, TEXT_DIM
    global BORDER, BORDER2, SIDEBAR_STYLE
    name = str(theme_name or "slate").strip().lower()
    if name not in _THEMES:
        name = "slate"
    CURRENT_THEME = name
    t = _THEMES[name]
    TEAL = t["accent_primary"]
    CYAN = t["accent_secondary"]
    VIOLET = t["accent_violet"]
    BLUE = t["accent_blue"]
    ROSE = t["danger"]
    AMBER = t["accent_warning"]
    BG = t["bg"]
    TEXT = t["text_primary"]
    TEXT_MID = t["text_secondary"]
    TEXT_DIM = t["text_dim"]
    BORDER = "rgba(255,255,255,0.10)"
    BORDER2 = "rgba(255,255,255,0.16)"


_apply_theme(CURRENT_THEME)


_THEME_EXPORTS = {
    "TEAL",
    "VIOLET",
    "CYAN",
    "ROSE",
    "AMBER",
    "BLUE",
    "BG",
    "BORDER",
    "BORDER2",
    "TEXT",
    "TEXT_MID",
    "TEXT_DIM",
    "FROST",
    "FROST2",
}


def _propagate_theme_exports():
    values = {k: globals().get(k) for k in _THEME_EXPORTS}
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        d = getattr(mod, "__dict__", None)
        if not d:
            continue
        for key, value in values.items():
            if key in d:
                d[key] = value


def set_theme_name(theme_name: str):
    _apply_theme(theme_name)
    _rebuild_sidebar_style()
    _propagate_theme_exports()


def _rgba(r: int, g: int, b: int, a: float) -> str:
    return f"rgba({r},{g},{b},{max(0.0, min(1.0, a)):.3f})"


def surface() -> str:
    return _rgba(255, 255, 255, SURFACE_ALPHA * 0.10)


def surface_elevated() -> str:
    return _rgba(255, 255, 255, SURFACE_ELEVATED_ALPHA * 0.12)


def surface_border() -> str:
    return _rgba(255, 255, 255, SURFACE_BORDER_ALPHA)


FROST = surface()
FROST2 = surface_elevated()


def set_surface_alpha(pct: int):
    global SURFACE_ALPHA, SURFACE_ELEVATED_ALPHA, FROST, FROST2
    t = max(72, min(100, int(pct)))
    k = (100 - t) / 28.0
    SURFACE_ALPHA = 0.30 + 0.45 * k
    SURFACE_ELEVATED_ALPHA = SURFACE_ALPHA + 0.10
    FROST = surface()
    FROST2 = surface_elevated()
    _propagate_theme_exports()


def set_motion_level(level: str):
    global MOTION_LEVEL
    MOTION_LEVEL = level if level in {"rich", "subtle", "static"} else "rich"


def _hex_to_rgb(hex_color: str):
    s = (hex_color or "").strip().lstrip("#")
    if len(s) != 6:
        return 12, 19, 32
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except Exception:
        return 12, 19, 32


def get_app_style(window_opacity_pct: int = 94) -> str:
    root_alpha = max(0.70, min(0.98, window_opacity_pct / 100.0))
    br, bg, bb = _hex_to_rgb(BG)
    return f"""
* {{
    outline: none;
}}
QMainWindow {{
    background: transparent;
}}
QWidget#root {{
    background: {_rgba(br, bg, bb, root_alpha)};
}}
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: 'Geist', 'IBM Plex Sans', 'Inter', sans-serif;
    font-size: 13px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    width: 4px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.10);
    border-radius: 2px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QToolTip {{
    background: rgba(7,12,23,240);
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
}}
QPushButton:focus {{
    outline: none;
}}
"""


APP_STYLE = get_app_style(94)
SIDEBAR_STYLE = ""


def _rebuild_sidebar_style():
    global SIDEBAR_STYLE
    SIDEBAR_STYLE = f"""
QWidget#sidebar {{
    background: {surface()};
    border-right: 1px solid {BORDER};
}}
QPushButton#sb-item {{
    background: transparent;
    border: none;
    border-radius: 12px;
    color: {TEXT_DIM};
    font-size: 16px;
    padding: 0;
    min-width: 0;
    min-height: 0;
}}
QPushButton#sb-item:hover {{
    background: rgba(255,255,255,0.06);
    color: {TEXT_MID};
}}
QPushButton#sb-item[active=true] {{
    background: {TEAL}18;
    color: {TEAL};
}}
QPushButton#sb-item:focus {{
    border: none;
}}
QLabel#sb-logo {{
    color: {TEAL};
    font-size: 20px;
    background: transparent;
    border: none;
}}
"""


_rebuild_sidebar_style()


def lbl(text, size=13, color=TEXT, bold=False, mono=False, wrap=False):
    l = QLabel(str(text))
    ff = "monospace" if mono else "inherit"
    fw = "600" if bold else "400"
    l.setStyleSheet(
        f"""
        color:{color};font-size:{size}px;font-weight:{fw};
        font-family:{ff};background:transparent;border:none;
    """
    )
    if wrap:
        l.setWordWrap(True)
    return l


def card_frame(accent=None, hover=True):
    f = QFrame()
    f.setProperty("pb_card", True)
    f.setProperty("pb_card_accent", bool(accent))
    f.setProperty("pb_card_hover", bool(hover))
    f.setStyleSheet(_card_style(bool(accent), bool(hover)))
    return f


def _card_style(accent=False, hover=True):
    border_color = f"{TEAL}44" if accent else surface_border()
    hover_style = (
        f"""
        QFrame:hover {{
            border-color: {'rgba(255,255,255,0.24)' if accent else BORDER2};
        }}
    """
        if hover
        else ""
    )
    return f"""
        QFrame {{
            background: {surface()};
            border: 1px solid {border_color};
            border-radius: 18px;
        }}
        {hover_style}
    """


def refresh_card_styles(root_widget):
    if root_widget is None:
        return
    for frame in root_widget.findChildren(QFrame):
        if not frame.property("pb_card"):
            continue
        accent = bool(frame.property("pb_card_accent"))
        hover = bool(frame.property("pb_card_hover"))
        frame.setStyleSheet(_card_style(accent, hover))


def input_field(placeholder="", password=False):
    w = QLineEdit()
    if password:
        w.setEchoMode(QLineEdit.EchoMode.Password)
    w.setPlaceholderText(placeholder)
    w.setStyleSheet(
        f"""
        QLineEdit {{
            background: {surface_elevated()};
            border: 1px solid {BORDER};
            border-radius: 10px;
            color: {TEXT};
            padding: 9px 13px;
            font-size: 12px;
        }}
        QLineEdit:focus {{
            border-color: {TEAL}88;
            background: rgba(255,255,255,0.08);
        }}
    """
    )
    return w


def text_area(placeholder="", height=80):
    w = QTextEdit()
    w.setPlaceholderText(placeholder)
    w.setFixedHeight(height)
    w.setStyleSheet(
        f"""
        QTextEdit {{
            background: {surface_elevated()};
            border: 1px solid {BORDER};
            border-radius: 10px;
            color: {TEXT};
            padding: 9px 13px;
            font-size: 12px;
        }}
        QTextEdit:focus {{
            border-color: {TEAL}88;
        }}
    """
    )
    return w


def _button_style(color, role="secondary"):
    if role == "danger":
        color = ROSE
    if role == "primary":
        bg = f"{TEAL}2E"
        border = f"{TEAL}70"
        hover_bg = f"{TEAL}40"
    elif role == "danger":
        bg = f"{ROSE}28"
        border = f"{ROSE}66"
        hover_bg = f"{ROSE}3F"
    else:
        bg = _rgba(255, 255, 255, 0.08)
        border = _rgba(255, 255, 255, 0.16)
        hover_bg = _rgba(255, 255, 255, 0.14)
    return f"""
        QPushButton {{
            background:{bg};border:1px solid {border};
            border-radius:10px;color:{color};
            padding:9px 14px;font-size:12px;font-weight:500;
        }}
        QPushButton:hover {{
            background:{hover_bg};border-color:{color}88;
        }}
        QPushButton:pressed {{
            background:{color}30;
        }}
        QPushButton:disabled {{
            color:{TEXT_DIM};
            border-color:rgba(255,255,255,0.10);
            background:rgba(255,255,255,0.04);
        }}
    """


def action_btn(text, color=TEAL, icon="", role="secondary"):
    b = QPushButton(f"{icon}  {text}" if icon else text)
    b.setStyleSheet(_button_style(color, role=role))
    return b


def primary_btn(text, icon=""):
    return action_btn(text, TEAL, icon=icon, role="primary")


def secondary_btn(text, icon=""):
    return action_btn(text, CYAN, icon=icon, role="secondary")


def danger_btn(text, icon=""):
    return action_btn(text, ROSE, icon=icon, role="danger")


class AnimatedSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, on=False, color=TEAL, parent=None):
        super().__init__(parent)
        self._checked = bool(on)
        self._color = color
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)

        self._track = QFrame(self)
        self._track.setGeometry(0, 0, 44, 24)
        self._track.setStyleSheet("border:1px solid rgba(255,255,255,0.18);border-radius:12px;")

        self._knob = QFrame(self)
        self._knob.setFixedSize(18, 18)
        self._knob.setStyleSheet("background:rgba(255,255,255,0.92);border:none;border-radius:9px;")
        self._glow = QGraphicsDropShadowEffect(self._knob)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(12)
        self._glow.setColor(QColor(0, 0, 0, 0))
        self._knob.setGraphicsEffect(self._glow)

        self._anim = QPropertyAnimation(self._knob, b"geometry", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(160)
        self._refresh(animated=False)

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        value = bool(value)
        if self._checked == value:
            self._refresh(animated=False)
            return
        self._checked = value
        self._refresh(animated=True)
        self.toggled.emit(self._checked)

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def _refresh(self, animated=True):
        on = bool(self._checked)
        active = TEAL
        track_bg = f"{active}30" if on else "rgba(255,255,255,0.08)"
        track_border = f"{active}88" if on else "rgba(255,255,255,0.16)"
        self._track.setStyleSheet(f"background:{track_bg};border:1px solid {track_border};border-radius:12px;")
        if on:
            self._knob.setStyleSheet(f"background:{active};border:none;border-radius:9px;")
            self._glow.setBlurRadius(14)
            glow = QColor(active)
            glow.setAlpha(210)
            self._glow.setColor(glow)
        else:
            self._knob.setStyleSheet("background:rgba(255,255,255,0.90);border:none;border-radius:9px;")
            self._glow.setBlurRadius(0)
            self._glow.setColor(QColor(0, 0, 0, 0))
        target = QRect(23, 3, 18, 18) if on else QRect(3, 3, 18, 18)
        if not animated or MOTION_LEVEL == "static":
            self._knob.setGeometry(target)
            return
        self._anim.stop()
        self._anim.setDuration(100 if MOTION_LEVEL == "subtle" else 160)
        self._anim.setStartValue(self._knob.geometry())
        self._anim.setEndValue(target)
        self._anim.start()


class ToggleProxy(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, on=False, color=TEAL, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._switch = AnimatedSwitch(on=on, color=color)
        self._switch.toggled.connect(self.toggled.emit)
        layout.addWidget(self._switch)

    def isChecked(self):
        return self._switch.isChecked()

    def setChecked(self, value):
        self._switch.setChecked(value)


def toggle_switch(on=False, color=TEAL):
    return ToggleProxy(on=on, color=color)


def pill(text, color=TEAL, pulse=False):
    w = QWidget()
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    w.setStyleSheet(
        f"""
        QWidget {{
            color:{color};
            background:{color}14;
            border:1px solid {color}33;
            border-radius:99px;
        }}
    """
    )
    row = QHBoxLayout(w)
    row.setContentsMargins(8, 2, 10, 2)
    row.setSpacing(6)
    w.setMinimumHeight(22)
    dot = QFrame()
    dot.setFixedSize(6, 6)
    dot.setStyleSheet(f"background:{color};border:none;border-radius:3px;")
    row.addWidget(dot)
    text_lbl = QLabel(f"{text}")
    text_lbl.setStyleSheet("font-size:10px;font-family:monospace;background:transparent;border:none;")
    row.addWidget(text_lbl)
    w._dot_widget = dot
    w._text_label = text_lbl
    if pulse:
        try:
            from ui.motion import breathe
            breathe(dot, level=MOTION_LEVEL)
        except Exception:
            pass
    return w


def section_label(text):
    l = QLabel(text.upper())
    l.setStyleSheet(
        f"""
        color:{TEXT_DIM};font-size:9px;letter-spacing:2px;
        font-family:monospace;font-weight:600;
        background:transparent;border:none;
        margin-bottom:2px;
    """
    )
    return l


def divider():
    d = QFrame()
    d.setFrameShape(QFrame.Shape.HLine)
    d.setFixedHeight(1)
    d.setStyleSheet(f"background:{BORDER};border:none;max-height:1px;")
    return d


class ToggleRow(QWidget):
    """A settings row with icon, label, description, and toggle"""

    toggled = pyqtSignal(bool)

    def __init__(self, icon, name, desc="", checked=False, color=TEAL, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;border:none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 11, 20, 11)
        layout.setSpacing(14)

        ico = lbl(icon, 17)
        ico.setFixedWidth(26)
        layout.addWidget(ico)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(lbl(name, 13, TEXT, bold=True))
        if desc:
            info.addWidget(lbl(desc, 11, TEXT_DIM))
        layout.addLayout(info)
        layout.addStretch()

        self.toggle = toggle_switch(checked, color)
        self.toggle.toggled.connect(self.toggled.emit)
        layout.addWidget(self.toggle)

    def is_checked(self):
        return self.toggle.isChecked()

    def set_checked(self, v):
        self.toggle.setChecked(v)


class InfoRow(QWidget):
    """A settings row with icon, label, value, and optional arrow"""

    clicked = pyqtSignal()

    def __init__(self, icon, name, desc="", value="", clickable=True, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;border:none;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 11, 20, 11)
        layout.setSpacing(14)

        ico = lbl(icon, 17)
        ico.setFixedWidth(26)
        layout.addWidget(ico)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(lbl(name, 13, TEXT, bold=True))
        if desc:
            info.addWidget(lbl(desc, 11, TEXT_DIM))
        layout.addLayout(info)
        layout.addStretch()

        if value:
            layout.addWidget(lbl(value, 11, TEXT_DIM, mono=True))

        if clickable:
            arrow = lbl("›", 18, TEXT_DIM)
            layout.addWidget(arrow)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)
