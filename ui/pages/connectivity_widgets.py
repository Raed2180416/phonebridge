"""Shared connectivity widgets and workers for Dashboard and Network pages."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ui.theme import AMBER, TEXT, TEXT_DIM, TEXT_MID, lbl, toggle_switch


class ToggleActionWorker(QThread):
    done = pyqtSignal(bool, str, object)

    def __init__(self, action):
        super().__init__()
        self._action = action

    def run(self):
        try:
            out = self._action()
            if isinstance(out, tuple) and len(out) >= 3:
                ok, msg, actual = out[0], out[1], out[2]
            elif isinstance(out, tuple) and len(out) == 2:
                ok, msg = out
                actual = None
            else:
                ok, msg, actual = bool(out), "", None
        except Exception as exc:
            ok, msg, actual = False, str(exc), None
        self.done.emit(bool(ok), str(msg or ""), actual)


def build_conn_row(ico, name, sub, on, color, on_toggle=None, *, icon_size=18, margins=(20, 11, 20, 11)):
    widget = QWidget()
    widget.setStyleSheet("background:transparent;border:none;")
    row = QHBoxLayout(widget)
    row.setContentsMargins(*margins)
    row.setSpacing(12)
    row.addWidget(lbl(ico, icon_size))
    info = QVBoxLayout()
    info.setSpacing(2)
    info.addWidget(lbl(name, 13, TEXT, bold=True))
    sub_lbl = lbl(sub, 11, TEXT_DIM)
    info.addWidget(sub_lbl)
    row.addLayout(info)
    row.addStretch()
    toggle = toggle_switch(on, color)
    if on_toggle:
        toggle.toggled.connect(lambda checked: on_toggle(checked))
    row.addWidget(toggle)
    widget._toggle = toggle
    widget._sub = sub_lbl
    return widget


def set_conn_toggle_state(row, checked):
    if not hasattr(row, "_toggle"):
        return
    toggle = row._toggle
    try:
        toggle.blockSignals(True)
        toggle.setChecked(bool(checked))
        toggle.blockSignals(False)
    except RuntimeError:
        pass


def set_conn_row_detail(row, detail=None):
    if detail is None or not hasattr(row, "_sub"):
        return
    try:
        row._sub.setText(str(detail))
    except RuntimeError:
        pass


def set_conn_row_state(row, enabled, detail=None):
    set_conn_toggle_state(row, enabled)
    set_conn_row_detail(row, detail)


def set_conn_row_busy(row, busy):
    if not hasattr(row, "_toggle"):
        return
    try:
        row._toggle.setEnabled(not busy)
    except RuntimeError:
        pass


def set_pill(widget, text, color):
    if widget is None:
        return
    widget.setStyleSheet(
        f"""
        QWidget {{
            color:{color};
            background:transparent;
            border:none;
            border-radius:0px;
        }}
    """
    )
    txt = getattr(widget, "_text_label", None)
    if txt is not None:
        txt.setText(text)
        txt.setStyleSheet(
            f"font-size:10px;font-family:monospace;background:transparent;border:none;color:{color};"
        )
    dot = getattr(widget, "_dot_widget", None)
    if dot is not None:
        dot.setStyleSheet(f"background:{color};border:none;border-radius:3px;")


def set_status_pill(widget, label, state_name):
    state_map = {
        "connected": (TEXT_MID, "Connected"),
        "connecting": (TEXT_DIM, "Checking"),
        "degraded": (AMBER, "Degraded"),
        "disconnected": (TEXT_DIM, "Offline"),
    }
    color, suffix = state_map.get(state_name, state_map["disconnected"])
    set_pill(widget, f"{label} · {suffix}", color)
