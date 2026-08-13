"""Environments page: what ``--env`` can select, and the per-run directories.

Environments are not a thing the launcher stores anywhere - they are *derived*
from the distinct ``env`` values in users.json (session_launcher.build_environments),
which is why this page reads them from ``--describe`` and edits them through the
config file rather than through some GUI-only registry.

The URL override and the default directories genuinely have no home in the core,
so those live in QSettings and are handed to the Command page as defaults.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import theme, widgets


class EnvironmentsPage(QWidget):
    """Read-only inventory of environments + the GUI's own per-env extras."""

    directories_changed = Signal()

    HEADERS = ["Alias", "env value (users.json)", "Origin", "Users", "URL override"]

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._envs = []

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(6)
        column.addWidget(widgets.heading("Environments"))
        column.addWidget(widgets.lede(
            "Read from --describe. The env value and the user rows live in users.json; "
            "URL overrides and default directories are stored by the GUI."))
        column.addSpacing(12)

        panel = widgets.BlueprintPanel(padding=(1, 1, 1, 1))
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        for index in range(len(self.HEADERS)):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setMinimumHeight(140)
        panel.layout().addWidget(self.table)
        column.addWidget(panel)

        column.addSpacing(18)
        column.addWidget(widgets.kicker("Default directories"))
        column.addWidget(widgets.lede(
            "Passed to every run as --flows-dir / --reports-dir / --sessions-dir. "
            "Empty means the core's own default."))
        column.addSpacing(6)

        self.dir_edits = {}
        dirs = QHBoxLayout()
        dirs.setSpacing(16)
        for key, flag in (("flows", "--flows-dir"), ("reports", "--reports-dir"),
                          ("sessions", "--sessions-dir")):
            edit = QLineEdit(self.settings.directory(key))
            edit.setProperty("mono", True)
            edit.setPlaceholderText("(core default)")
            edit.editingFinished.connect(
                lambda k=key, e=edit: self._save_directory(k, e.text()))
            browse = QPushButton(theme.glyph("browse"))
            browse.setFixedWidth(38)
            browse.clicked.connect(lambda _c=False, k=key, e=edit: self._browse(k, e))
            self.dir_edits[key] = edit
            dirs.addWidget(widgets.field(flag, widgets.row(edit, browse,
                                                           stretch_last=False)), 1)
        column.addLayout(dirs)
        column.addStretch(1)

    # -- data -----------------------------------------------------------------
    def set_inventory(self, inventory):
        self._envs = inventory.envs
        self.table.setRowCount(len(self._envs))
        for row, env in enumerate(self._envs):
            alias = env.get("alias", "")
            self._set(row, 0, alias, mono=True)
            self._set(row, 1, env.get("value", ""), mono=True)
            self._set(row, 2, env.get("origin", "") or "(no URL)", mono=True,
                      color=theme.ACCENT_RAMP[700] if env.get("origin")
                      else theme.NEUTRAL[600])
            self._set(row, 3, str(env.get("count", "")))
            override = QLineEdit(self.settings.env_override(alias))
            override.setProperty("mono", True)
            override.setPlaceholderText("inherit")
            override.editingFinished.connect(
                lambda a=alias, e=override: self.settings.set_env_override(a, e.text()))
            self.table.setCellWidget(row, 4, override)
        self.table.resizeColumnsToContents()
        if self.table.columnWidth(4) < 240:
            self.table.setColumnWidth(4, 240)
        # Directory defaults follow the core unless the user set their own.
        for key, value in inventory.dirs().items():
            edit = self.dir_edits.get(key)
            if edit is not None and not edit.text():
                edit.setPlaceholderText(value or "(core default)")

    def _set(self, row, col, text, mono=False, color=None):
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if mono:
            item.setFont(theme.mono_font(9))
        if color:
            item.setForeground(theme.color(color))
        self.table.setItem(row, col, item)

    def override_for(self, alias):
        return self.settings.env_override(alias)

    def directories(self):
        return {key: edit.text().strip() for key, edit in self.dir_edits.items()}

    # -- editing --------------------------------------------------------------
    def _save_directory(self, key, value):
        self.settings.set_directory(key, value.strip())
        self.directories_changed.emit()

    def _browse(self, key, edit):
        start = edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Choose a directory", start)
        if chosen:
            edit.setText(chosen)
            self._save_directory(key, chosen)
