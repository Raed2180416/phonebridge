"""Motion helpers for consistent animated interactions."""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QPoint
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget


def _duration(level: str, subtle_ms: int, rich_ms: int) -> int:
    if level == "static":
        return 1
    return rich_ms if level == "rich" else subtle_ms


def fade_in(widget: QWidget, level: str = "rich", start=0.0, end=1.0):
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(start)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(_duration(level, 140, 200))
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def slide_and_fade_in(widget: QWidget, level: str = "rich", offset_y: int = 14):
    fade = fade_in(widget, level=level, start=0.0, end=1.0)
    start = widget.pos() + QPoint(0, offset_y)
    end = widget.pos()
    widget.move(start)
    move = QPropertyAnimation(widget, b"pos", widget)
    move.setDuration(_duration(level, 160, 220))
    move.setStartValue(start)
    move.setEndValue(end)
    move.setEasingCurve(QEasingCurve.Type.OutCubic)
    move.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return fade, move


def breathe(widget: QWidget, level: str = "rich", min_opacity: float = 0.35, max_opacity: float = 1.0):
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(max_opacity)
    if level == "static":
        return None
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(_duration(level, 1500, 2200))
    anim.setStartValue(max_opacity)
    anim.setKeyValueAt(0.5, min_opacity)
    anim.setEndValue(max_opacity)
    anim.setLoopCount(-1)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.start()
    widget._pb_breathe_anim = anim
    return anim
