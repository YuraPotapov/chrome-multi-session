"""Shared widgets that carry the design system's distinctive parts.

Qt stylesheets cover colour, type and borders; the pieces here are the ones a
stylesheet cannot express - the blueprint frame's registration marks, the
segmented control, the tag pill - plus small constructors so pages read as
layout rather than as widget configuration.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme


class BlueprintPanel(QFrame):
    """A plain hairline-bordered box.

    The design's corner registration marks (a small cross at each corner) were
    dropped: at this density they read as stray plus signs floating next to the
    frame rather than as draughting marks, and they collided with the controls
    sitting near the edges. The square, borderless-radius frame carries the
    blueprint look on its own.
    """

    def __init__(self, parent=None, padding=(16, 16, 16, 16)):
        super().__init__(parent)
        self.setProperty("role", "panel")
        self.setContentsMargins(0, 0, 0, 0)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*padding)
        self._layout.setSpacing(10)

    def layout(self):
        return self._layout


class Tag(QLabel):
    """Small status pill: accent (good), outline (in flight), neutral, bad."""

    STYLES = {
        "accent": (theme.ACCENT_RAMP[100], theme.ACCENT_RAMP[800], "transparent"),
        "outline": ("transparent", theme.ACCENT, theme.ACCENT),
        "neutral": (theme.NEUTRAL[100], theme.NEUTRAL[800], theme.NEUTRAL[300]),
        "warn": ("#f7efe2", theme.WARN, "transparent"),
        "bad": ("#f7e7e5", theme.BAD, "transparent"),
    }

    def __init__(self, text="", variant="neutral", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_variant(variant)

    def set_variant(self, variant):
        background, fg, border = self.STYLES.get(variant, self.STYLES["neutral"])
        self.setStyleSheet(
            "background: %s; color: %s; border: 1px solid %s;"
            "font-family: %s; font-size: 10px; letter-spacing: 0.5px;"
            "padding: 2px 8px;" % (background, fg, border, theme.MONO_CSS))

    def set(self, text, variant):
        self.setText(text)
        self.set_variant(variant)


class Segmented(QWidget):
    """A row of mutually exclusive buttons (the design's `.seg`).

    Used where a QComboBox would hide the options: log levels, log filters -
    short, fixed vocabularies that are worth showing all at once.
    """

    changed = Signal(str)

    def __init__(self, options, current=None, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._buttons = {}
        for option in options:
            button = QPushButton(option, self)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, o=option: self.set_current(o))
            row.addWidget(button)
            self._buttons[option] = button
        row.addStretch(1)
        self.set_current(current or (options[0] if options else None), notify=False)

    def set_current(self, option, notify=True):
        self._current = option
        for name, button in self._buttons.items():
            active = name == option
            button.setChecked(active)
            button.setStyleSheet(
                "background: %s; color: %s; border: 1px solid %s; padding: 3px 11px;"
                "font-family: %s; font-size: 12px; font-weight: 500;"
                % (theme.ACCENT if active else "transparent",
                   theme.BG if active else theme.TEXT,
                   theme.ACCENT if active else theme.DIVIDER, theme.BODY_CSS))
        if notify:
            self.changed.emit(option)

    def current(self):
        return self._current


def heading(text, role="h1"):
    label = QLabel(text)
    label.setProperty("role", role)
    return label


def kicker(text):
    return heading(text.upper(), "kicker")


def lede(text):
    label = QLabel(text)
    label.setProperty("role", "lede")
    label.setWordWrap(True)
    return label


def mono(text=""):
    label = QLabel(text)
    label.setProperty("role", "mono")
    return label


def hline():
    line = QFrame()
    line.setProperty("role", "hline")
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return line


def vline(height=20):
    line = QFrame()
    line.setProperty("role", "vline")
    line.setFixedSize(1, height)
    return line


def field(label_text, widget, hint=None):
    """A labelled control: the design's `.field` (small label above the input)."""
    box = QWidget()
    column = QVBoxLayout(box)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    label = QLabel(label_text)
    label.setStyleSheet("font-size: 11px; color: %s; font-family: %s;"
                        % (theme.NEUTRAL[700], theme.MONO_CSS))
    column.addWidget(label)
    column.addWidget(widget)
    if hint:
        note = QLabel(hint)
        note.setStyleSheet("font-size: 11px; color: %s;" % theme.NEUTRAL[600])
        note.setWordWrap(True)
        column.addWidget(note)
    return box


def row(*widgets, spacing=8, stretch_last=False):
    """A horizontal strip; ints become fixed spacers, None becomes a stretch."""
    box = QWidget()
    line = QHBoxLayout(box)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(spacing)
    for widget in widgets:
        if widget is None:
            line.addStretch(1)
        elif isinstance(widget, int):
            line.addSpacing(widget)
        else:
            line.addWidget(widget)
    if stretch_last and line.count():
        line.setStretch(line.count() - 1, 1)
    return box
