"""Settings dialog: where the core is, and which interpreter runs it.

This is the whole configuration surface of the GUI. Everything else it knows
comes from the core itself, which is why the dialog's own feedback is simply
"can I run --describe against this, and what did it say?".
"""

import os
import sys

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout)

from .. import core as core_mod
from .. import theme, widgets


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 20, 24, 18)
        column.setSpacing(6)
        column.addWidget(widgets.heading("Settings", "h2"))
        column.addWidget(widgets.lede(
            "Stored by the GUI. It never imports the core - it spawns the launcher "
            "through this interpreter, so the two environments stay independent."))
        column.addSpacing(12)

        detected_script, detected_python = core_mod.autodetect()
        current_script = settings.core_script or detected_script or ""
        self.script = QLineEdit(current_script)
        self.script.setProperty("mono", True)
        self.script.setPlaceholderText("path to session_launcher.py")
        column.addWidget(widgets.field("Core script",
                                       widgets.row(self.script,
                                                   self._browse_button(self._pick_script))))

        # An installed build's core is an executable, so there is no interpreter to
        # choose - leave the field empty and say why rather than offering a Python
        # that could not run it anyway.
        packaged = not core_mod.needs_interpreter(current_script)
        self.interpreter = QLineEdit(
            "" if packaged else (settings.interpreter or detected_python or sys.executable))
        self.interpreter.setProperty("mono", True)
        self.interpreter.setPlaceholderText(
            "not needed: the core is a packaged executable" if packaged
            else "python that has playwright + cryptography")
        self.interpreter_browse = self._browse_button(self._pick_interpreter)
        self.interpreter.setEnabled(not packaged)
        self.interpreter_browse.setEnabled(not packaged)
        column.addWidget(widgets.field(
            "Interpreter", widgets.row(self.interpreter, self.interpreter_browse),
            "Only used for a session_launcher.py; a packaged core runs itself. "
            "The core's own .venv is detected automatically when gui/ sits inside "
            "the core checkout."))

        self.config = QLineEdit(settings.config)
        self.config.setProperty("mono", True)
        self.config.setPlaceholderText("users.json (the core's default)")
        column.addWidget(widgets.field(
            "Config (--config)", widgets.row(self.config,
                                             self._browse_button(self._pick_config)),
            "Passed to every command, so the GUI and the CLI always read the same file."))

        column.addSpacing(10)
        test = QPushButton("Test connection")
        test.clicked.connect(self._test)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        column.addWidget(widgets.row(test, None))
        column.addWidget(self.result)

        column.addSpacing(10)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Save).setProperty("variant", "primary")
        column.addWidget(buttons)

    def _browse_button(self, slot):
        button = QPushButton("Browse…")
        button.clicked.connect(slot)
        return button

    def _pick_script(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "session_launcher.py", os.path.dirname(self.script.text() or "") or
            os.path.expanduser("~"), "Python (*.py);;All files (*)")
        if path:
            self.script.setText(path)
            guess = core_mod._venv_python(os.path.dirname(path))
            if guess and not self.interpreter.text():
                self.interpreter.setText(guess)

    def _pick_interpreter(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Python interpreter", os.path.dirname(self.interpreter.text() or "")
            or os.path.expanduser("~"), "All files (*)")
        if path:
            self.interpreter.setText(path)

    def _pick_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "users.json", os.path.dirname(self.config.text() or "") or
            os.path.expanduser("~"), "JSON (*.json);;All files (*)")
        if path:
            self.config.setText(path)

    def _test(self):
        core = self.core()
        try:
            payload = core.describe()
        except core_mod.CoreError as exc:
            self.result.setText(str(exc))
            self.result.setStyleSheet("color: %s; font-size: 12px;" % theme.BAD)
            return
        inventory = core_mod.Inventory(payload)
        self.result.setText("core %s · %d environments · %d users · %d scenarios%s"
                            % (inventory.version, len(inventory.envs),
                               len(inventory.users), len(inventory.scenarios),
                               "\n" + "\n".join(inventory.warnings)
                               if inventory.warnings else ""))
        self.result.setStyleSheet("color: %s; font-size: 12px;" % theme.ACCENT_RAMP[700])

    def core(self):
        return core_mod.Core(self.script.text().strip(),
                             self.interpreter.text().strip(),
                             self.config.text().strip())

    def apply(self):
        self.settings.core_script = self.script.text().strip()
        self.settings.interpreter = self.interpreter.text().strip()
        self.settings.config = self.config.text().strip()
