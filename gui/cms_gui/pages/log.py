"""Log page: the launcher's stderr, filterable.

The core already tags each line with the session that produced it during
parallel runs (``[dev-agent] ...``), so per-session filtering costs nothing but
a regex that is already applied when the line arrives.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .. import theme, widgets

LEVEL_COLOR = {
    "DEBUG": theme.NEUTRAL[500],
    "INFO": theme.NEUTRAL[800],
    "WARNING": theme.WARN,
    "ERROR": theme.BAD,
    "CRITICAL": theme.BAD,
}
LEVEL_ORDER = ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"]


class LogPage(QWidget):
    """Everything the launcher wrote to stderr, in order, with a filter."""

    MAX_LINES = 20000      # a long parallel run is chatty; keep memory bounded

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines = []
        self._sessions = []

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        head = QWidget()
        head.setStyleSheet("border-bottom: 1px solid %s;" % theme.DIVIDER)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(24, 14, 24, 12)
        self.level = widgets.Segmented(LEVEL_ORDER, "ALL")
        self.level.changed.connect(lambda _v: self._rerender())
        self.session = QComboBox()
        self.session.addItem("all sessions")
        self.session.currentIndexChanged.connect(lambda _i: self._rerender())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setFixedWidth(190)
        self.search.textChanged.connect(lambda _t: self._rerender())
        self.follow = QCheckBox("Follow")
        self.follow.setChecked(True)
        save = QPushButton("Save…")
        save.clicked.connect(self.save_to_file)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        head_layout.addWidget(widgets.row(
            widgets.heading("Log"),
            widgets.lede("stderr of the launcher process"), None,
            self.level, self.session, self.search, self.follow, save, clear))
        column.addWidget(head)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(self.MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setStyleSheet(
            "background: %s; border: none; font-family: %s; font-size: 12px;"
            % (theme.NEUTRAL[100], theme.MONO_CSS))
        column.addWidget(self.view, 1)

    # -- input ----------------------------------------------------------------
    def append(self, record):
        self._lines.append(record)
        if len(self._lines) > self.MAX_LINES:
            del self._lines[:len(self._lines) - self.MAX_LINES]
        session = record.get("session", "")
        if session and session not in self._sessions:
            self._sessions.append(session)
            self.session.addItem(session)
        if self._passes(record):
            self._write(record)

    def clear(self):
        self._lines = []
        self.view.clear()

    # -- filtering ------------------------------------------------------------
    def _passes(self, record):
        level = self.level.current()
        if level != "ALL" and record.get("level") != level:
            return False
        chosen = self.session.currentText()
        if self.session.currentIndex() > 0 and record.get("session") != chosen:
            return False
        needle = self.search.text().strip().lower()
        if needle:
            haystack = (record.get("text", "") + record.get("session", "")).lower()
            if needle not in haystack:
                return False
        return True

    def _rerender(self):
        self.view.clear()
        for record in self._lines:
            if self._passes(record):
                self._write(record)

    def _write(self, record):
        level = record.get("level", "")
        colour = LEVEL_COLOR.get(level, theme.NEUTRAL[700])
        session = record.get("session", "")
        # A rich-text append per line is affordable at this volume and keeps the
        # level/session colouring the design asks for.
        html = ('<span style="color:%s">%s</span> '
                '<span style="color:%s">%-7s</span> '
                '<span style="color:%s">%s</span> '
                '<span style="color:%s">%s</span>'
                % (theme.NEUTRAL[500], _escape(record.get("ts", "")),
                   colour, _escape(level),
                   theme.ACCENT_RAMP[700], _escape("[%s]" % session if session else ""),
                   theme.NEUTRAL[800], _escape(record.get("text", ""))))
        self.view.appendHtml(html)
        if self.follow.isChecked():
            self.view.moveCursor(QTextCursor.End)

    # -- export ---------------------------------------------------------------
    def save_to_file(self):
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save log", os.path.join(os.path.expanduser("~"), "launcher.log"),
            "Log files (*.log *.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                for record in self._lines:
                    session = ("[%s] " % record["session"]) if record.get("session") else ""
                    fh.write("%s %-7s %s%s\n" % (record.get("ts", ""),
                                                 record.get("level", ""), session,
                                                 record.get("text", "")))
        except OSError as exc:
            QMessageBox.warning(self, "Save log", "Could not write %s:\n%s" % (path, exc))


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace(" ", "&nbsp;"))
