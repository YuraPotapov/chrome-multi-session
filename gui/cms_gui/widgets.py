"""Shared widgets that carry the design system's distinctive parts.

Qt stylesheets cover colour, type and borders; the pieces here are the ones a
stylesheet cannot express - the blueprint frame's registration marks, the
segmented control, the tag pill - plus small constructors so pages read as
layout rather than as widget configuration.
"""

import itertools

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from . import icons, theme


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
        "bad": (theme.BAD_TINT, theme.BAD, "transparent"),
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


class Stepper(QWidget):
    """A small number with a button on each side: [-][ 4 ][+].

    The stock spin box puts two 7px arrows stacked inside the frame - a target
    nobody can hit, and the first thing anyone notices about a form built out of
    them. Here each end is a full-height button, the number sits in the middle
    and is still typeable, and the three pieces share their hairlines so the
    control reads as one object.

    It carries the part of QSpinBox's interface the pages use, so it can stand in
    for one: :meth:`value`, :meth:`setValue`, :meth:`setRange` and the
    ``valueChanged`` signal.
    """

    valueChanged = Signal(int)

    def __init__(self, minimum=0, maximum=99, value=None, width=52, parent=None):
        super().__init__(parent)
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)

        self.down = self._button("minus", "left", -1)
        self.spin = QSpinBox()
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignCenter)
        self.spin.setRange(minimum, maximum)
        self.spin.setFixedWidth(width)
        self.up = self._button("plus", "right", +1)

        line.addWidget(self.down)
        line.addWidget(self.spin)
        line.addWidget(self.up)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.spin.valueChanged.connect(self._on_value)
        self.setValue(minimum if value is None else value)
        self._match_heights()

    def _button(self, mark, edge, step):
        button = icons.button(QPushButton(""), mark)
        button.setProperty("variant", "step")
        button.setProperty("edge", edge)
        button.setCursor(Qt.PointingHandCursor)
        # The number keeps the focus: tabbing onto a plus sign helps nobody.
        button.setFocusPolicy(Qt.NoFocus)
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(400)
        button.setAutoRepeatInterval(90)
        button.clicked.connect(lambda _checked=False, s=step: self.step_by(s))
        return button

    def _match_heights(self):
        height = self.spin.sizeHint().height()
        for button in (self.down, self.up):
            button.setFixedHeight(height)

    # -- the QSpinBox interface the pages use ---------------------------------
    def value(self):
        return self.spin.value()

    def setValue(self, value):
        self.spin.setValue(int(value))
        self._update_limits()

    def setRange(self, minimum, maximum):
        self.spin.setRange(minimum, maximum)
        self._update_limits()

    def step_by(self, step):
        self.spin.setValue(self.spin.value() + step)

    def _on_value(self, value):
        self._update_limits()
        self.valueChanged.emit(value)

    def _update_limits(self):
        """Grey the end that would do nothing, rather than let it click on."""
        self.down.setEnabled(self.spin.value() > self.spin.minimum())
        self.up.setEnabled(self.spin.value() < self.spin.maximum())


class ElidedLabel(QLabel):
    """A one-line label that shortens its text instead of widening the window.

    A QLabel's minimum width is the width of its text, and a layout honours that:
    one full filesystem path in a status bar or a header is enough to push the
    whole window wider than the screen, which is how the Artifacts tree ended up
    crushed to a sliver beside a path label. Eliding in the middle keeps the two
    ends - the run folder and the file name are the parts anyone reads - and the
    whole string stays available as the tooltip.
    """

    def __init__(self, text="", mode=Qt.ElideMiddle, parent=None):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self._eliding = False
        # Ignored, not Preferred: the point is that this label's own text must
        # never be what decides how wide anything gets.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = "" if text is None else str(text)
        self.setToolTip(self._full)
        self._elide()

    def text(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        if self._eliding:
            return          # super().setText can trigger a resize, which re-enters
        self._eliding = True
        try:
            width = self.width() - 2
            if width <= 0:
                super().setText(self._full)
            else:
                super().setText(self.fontMetrics().elidedText(self._full, self._mode,
                                                              width))
        finally:
            self._eliding = False


class CheckList(QWidget):
    """A searchable list of tick boxes, with a count of what is ticked.

    Accounts, extensions and scenarios are all the same problem - "choose some
    of these, and there may be thirty of them" - so they are all the same widget.
    The search box hides rows rather than removing them, so a ticked row that
    scrolls out of the filter is still part of the selection.
    """

    changed = Signal()

    def __init__(self, searchable=True, placeholder="Search…", parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.textChanged.connect(self.set_filter)
        self.search.setVisible(searchable)
        column.addWidget(self.search)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setMinimumHeight(140)
        self.list.itemChanged.connect(self._on_item_changed)
        column.addWidget(self.list, 1)

        self.count = QLabel("")
        self.count.setStyleSheet("font-size: 11px; color: %s; font-family: %s;"
                                 % (theme.NEUTRAL[600], theme.MONO_CSS))
        column.addWidget(self.count)
        self._quiet = False
        self._noun = "selected"

    # -- contents -------------------------------------------------------------
    def set_noun(self, noun):
        """What the count line calls the things in the list."""
        self._noun = noun
        self._update_count()

    def clear(self):
        self._quiet = True
        self.list.clear()
        self._quiet = False
        self._update_count()

    def add(self, value, label=None, note="", enabled=True, accent=False):
        item = QListWidgetItem("%-30s %s" % (label if label is not None else value,
                                             note))
        item.setFont(theme.mono_font(9))
        item.setData(Qt.UserRole, value)
        flags = item.flags() | Qt.ItemIsUserCheckable
        if not enabled:
            # An unusable row still has to be visible - the reason it cannot be
            # picked is the useful part - so it is shown greyed rather than hidden.
            flags &= ~Qt.ItemIsEnabled
        item.setFlags(flags)
        item.setCheckState(Qt.Unchecked)
        if accent:
            item.setForeground(theme.color(theme.ACCENT_RAMP[700]))
        elif not enabled:
            item.setForeground(theme.color(theme.NEUTRAL[500]))
        self._quiet = True
        self.list.addItem(item)
        self._quiet = False
        self._update_count()
        return item

    # -- selection ------------------------------------------------------------
    def values(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def checked(self):
        return [self.list.item(i).data(Qt.UserRole)
                for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def set_checked(self, values, notify=False):
        wanted = set(values or ())
        self._quiet = True
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in wanted
                               else Qt.Unchecked)
        self._quiet = False
        self._update_count()
        if notify:
            self.changed.emit()

    def set_all(self, checked):
        self._quiet = True
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.flags() & Qt.ItemIsEnabled:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._quiet = False
        self._update_count()
        self.changed.emit()

    # -- filtering ------------------------------------------------------------
    def set_filter(self, text):
        needle = (text or "").strip().lower()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())
        self._update_count()

    def _on_item_changed(self, _item):
        self._update_count()
        if not self._quiet:
            self.changed.emit()

    def _update_count(self):
        total = self.list.count()
        hidden = sum(1 for i in range(total) if self.list.item(i).isHidden())
        text = "%d of %d %s" % (len(self.checked()), total, self._noun)
        if hidden:
            text += "   ·   %d hidden by the search" % hidden
        self.count.setText(text)


class Disclosure(QWidget):
    """A titled section that folds away, for settings most runs never touch.

    Advanced options have to be reachable without being in the way: a collapsed
    section keeps them one click deep instead of pushing them onto another page
    or behind a mode switch.
    """

    toggled = Signal(bool)

    def __init__(self, title, expanded=False, parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)

        self._title = title
        self.button = QPushButton("")
        self.button.setProperty("variant", "ghost")
        self.button.setCheckable(True)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.toggled.connect(self._on_toggled)
        # A row rather than the button alone, so a control can be put on the
        # title's line without being put beside the whole section - see
        # add_to_header.
        self._header = QWidget()
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.addWidget(self.button, 0, Qt.AlignLeft)
        header_row.addStretch(1)
        column.addWidget(self._header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        column.addWidget(self._body)

        self.button.setChecked(expanded)
        self._on_toggled(expanded)

    def body(self):
        return self._body_layout

    def add_to_header(self, widget):
        """Put a control at the right-hand end of the title's own line.

        Not the same as standing the whole section next to it. A Disclosure is
        header and body stacked inside one widget, so a hbox holding the two
        side by side takes the control's width off the *body* as well - which is
        how the Server log's box ended up 150px narrower than the card it sits
        in, for a button that shares none of its line.
        """
        self._header.layout().addWidget(widget, 0, Qt.AlignTop)
        return widget

    def set_expanded(self, expanded):
        self.button.setChecked(bool(expanded))

    def is_expanded(self):
        return self.button.isChecked()

    def _on_toggled(self, checked):
        self._body.setVisible(checked)
        # A chevron that turns: pointing down at an open section, right at a
        # folded one. Whatever the mark, it has to say "this opens" while the
        # section is shut, which is the state most people meet it in.
        icons.button(self.button,
                     "disclosure_open" if checked else "disclosure_closed",
                     self._title)
        self.toggled.emit(checked)


_scope_counter = itertools.count()


def scoped_style(widget, css):
    """Apply ``css`` to ``widget`` alone - never to what is inside it.

    A stylesheet set on a widget also applies to its children, and it outranks
    the application sheet. So a plain ``background:`` on a container silently
    repaints every button in it: that is how the primary RUN button in the
    preview strip ended up filled with the strip's own colour, painting its
    near-white label on a near-white background. Scoping the rule to the
    widget's own object name confines it.
    """
    name = widget.objectName() or "scoped%d" % next(_scope_counter)
    widget.setObjectName(name)
    widget.setStyleSheet("#%s { %s }" % (name, css))
    return widget


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


def elided_mono(text="", mode=Qt.ElideMiddle):
    """Monospace machine text that may be a long path. See :class:`ElidedLabel`."""
    label = ElidedLabel(text, mode)
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


class Phrasing:
    """The same thing said two ways: plainly, and with the flag named.

    Developer mode is the switch between the two interaction levels, and the
    wording belongs to a level as much as the pages do. "--flows-dir" is noise
    to someone launching sessions and the only thing worth knowing to someone
    building a command line, so a string that names a flag is written twice and
    the mode picks which one is showing. Nothing is hidden either way: the plain
    wording says what the control does, not less.

    A page collects its pairs as it builds - each call sets the plain wording and
    hands the widget straight back, so it still reads as one line - and applies
    them from its own ``set_developer_mode``. The mode itself is the window's.
    """

    def __init__(self):
        self._entries = []

    def text(self, widget, plain, developer):
        """Register a widget's label text. Returns the widget, ready to add."""
        self._entries.append((widget.setText, plain, developer))
        widget.setText(plain)
        return widget

    def apply(self, developer):
        for setter, plain, spelled_out in self._entries:
            setter(spelled_out if developer else plain)


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
    # Kept reachable so a form can relabel a control whose meaning depends on
    # another answer - a log's target is a path, a container, a unit or a URL.
    box.label = label
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
