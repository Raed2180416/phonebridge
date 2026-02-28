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
)
from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QRect

# Theme palettes
_THEMES = {
    "slate": {
        "accent_primary": "#53D1FF",
        "accent_secondary": "#C4B5FD",
        "accent_success": "#34D399",
        "accent_warning": "#FBBF24",
        "danger": "#FB7185",
        "text_primary": "rgba(241,245,255,0.95)",
        "text_secondary": "rgba(211,222,242,0.72)",
        "text_dim": "rgba(198,210,234,0.48)",
        "bg": "#0B1321",
    },
    "mist": {
        "accent_primary": "#53D1FF",
        "accent_secondary": "#C4B5FD",
        "accent_success": "#6EE7B7",
        "accent_warning": "#FCD34D",
        "danger": "#FB7185",
        "text_primary": "rgba(242,247,255,0.95)",
        "text_secondary": "rgba(222,232,246,0.73)",
        "text_dim": "rgba(203,216,235,0.50)",
        "bg": "#101B2A",
    },
    "night": {
        "accent_primary": "#53D1FF",
        "accent_secondary": "#C4B5FD",
        "accent_success": "#34D399",
        "accent_warning": "#FBBF24",
        "danger": "#F43F5E",
        "text_primary": "rgba(237,244,255,0.96)",
        "text_secondary": "rgba(205,217,242,0.72)",
        "text_dim": "rgba(189,203,232,0.46)",
        "bg": "#090F1D",
    },
}

CURRENT_THEME = "slate"

# Backward-compatible color names
TEAL = "#53D1FF"
VIOLET = "#A78BFA"
CYAN = "#53D1FF"
ROSE = "#FB7185"
AMBER = "#FBBF24"
BLUE = "#93C5FD"
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
    global CURRENT_THEME, TEAL, CYAN, VIOLET, ROSE, AMBER, BG, TEXT, TEXT_MID, TEXT_DIM
    global BORDER, BORDER2, SIDEBAR_STYLE
    name = str(theme_name or "slate").strip().lower()
    if name not in _THEMES:
        name = "slate"
    CURRENT_THEME = name
    t = _THEMES[name]
    TEAL = t["accent_primary"]
    CYAN = t["accent_primary"]
    VIOLET = t.get("accent_secondary", "#A78BFA")
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


def with_alpha(hex_color: str, alpha: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgba(r, g, b, alpha)


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
    background: rgba(255,255,255,0.14);
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
    border-radius: 8px;
    color: transparent;
    font-size: 0px;
    font-weight: 400;
    padding: 0;
    margin: 0;
    min-width: 0;
    min-height: 0;
    outline: none;
}}
QPushButton#sb-item:hover {{
    background: transparent;
    border: none;
}}
QPushButton#sb-item[active=true] {{
    background: transparent;
    border: none;
}}
QPushButton#sb-item:focus {{
    border: none;
}}
QLabel#sb-logo {{
    color: {VIOLET};
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
    border_color = with_alpha(TEAL, 0.28) if accent else surface_border()
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
            border-color: {with_alpha(TEAL, 0.56)};
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
            border-color: {with_alpha(TEAL, 0.56)};
        }}
    """
    )
    return w


def _button_style(color, role="secondary"):
    if role == "danger":
        color = ROSE
    if role == "primary":
        color = VIOLET
        bg = with_alpha(VIOLET, 0.20)
        border = with_alpha(VIOLET, 0.46)
        hover_bg = with_alpha(VIOLET, 0.28)
        pressed_bg = with_alpha(VIOLET, 0.34)
        hover_border = with_alpha(VIOLET, 0.58)
    elif role == "danger":
        bg = with_alpha(ROSE, 0.14)
        border = with_alpha(ROSE, 0.34)
        hover_bg = with_alpha(ROSE, 0.22)
        pressed_bg = with_alpha(ROSE, 0.30)
        hover_border = with_alpha(ROSE, 0.50)
    else:
        color = color or CYAN
        bg = _rgba(255, 255, 255, 0.05)
        border = _rgba(255, 255, 255, 0.14)
        hover_bg = with_alpha(VIOLET, 0.12)
        pressed_bg = with_alpha(VIOLET, 0.18)
        hover_border = with_alpha(VIOLET, 0.42)
    return f"""
        QPushButton {{
            background:{bg};border:1px solid {border};
            border-radius:10px;color:{color};
            padding:9px 14px;font-size:12px;font-weight:500;
        }}
        QPushButton:hover {{
            background:{hover_bg};border-color:{hover_border};
        }}
        QPushButton:pressed {{
            background:{pressed_bg};border-color:{hover_border};
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

    def __init__(self, on=False, color=VIOLET, parent=None):
        super().__init__(parent)
        self._checked = bool(on)
        # Keep toggles visually deterministic across the app.
        self._color = "#a78bfa"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)

        self._track = QFrame(self)
        self._track.setGeometry(0, 0, 44, 24)
        self._track.setStyleSheet("border:1px solid rgba(255,255,255,0.18);border-radius:12px;")

        self._knob = QFrame(self)
        self._knob.setFixedSize(18, 18)
        self._knob.setStyleSheet("background:rgba(255,255,255,0.92);border:none;border-radius:9px;")

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
        active = "#a78bfa"
        track_bg = with_alpha(active, 0.26) if on else "rgba(255,255,255,0.08)"
        track_border = with_alpha(active, 0.58) if on else "rgba(255,255,255,0.16)"
        self._track.setStyleSheet(f"background:{track_bg};border:1px solid {track_border};border-radius:12px;")
        self._knob.setStyleSheet(
            f"background:{active if on else 'rgba(255,255,255,0.92)'};border:none;border-radius:9px;"
        )
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

    def __init__(self, on=False, color=VIOLET, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._switch = AnimatedSwitch(on=on, color=VIOLET)
        self._switch.toggled.connect(self.toggled.emit)
        layout.addWidget(self._switch)

    def isChecked(self):
        return self._switch.isChecked()

    def setChecked(self, value):
        self._switch.setChecked(value)


def toggle_switch(on=False, color=VIOLET):
    return ToggleProxy(on=on, color=VIOLET)


def pill(text, color=TEAL, pulse=False):
    w = QWidget()
    w.setStyleSheet(
        f"""
        QWidget {{
            color:{color};
            background:rgba(255,255,255,0.04);
            border:1px solid rgba(255,255,255,0.12);
            border-radius:99px;
        }}
    """
    )
    row = QHBoxLayout(w)
    row.setContentsMargins(8, 2, 10, 2)
    row.setSpacing(6)
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

    def __init__(self, icon, name, desc="", checked=False, color=VIOLET, parent=None):
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

        self.toggle = toggle_switch(checked, VIOLET)
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
