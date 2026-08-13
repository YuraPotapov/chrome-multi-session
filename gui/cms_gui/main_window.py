"""The main window: toolbar, CONFIGURE/OBSERVE sidebar, pages, status bar.

Layout follows the design export: a run/stop toolbar across the top, a narrow
navigation rail split into what you *configure* and what you *observe*, and a
status bar that always says which core, interpreter and config are in play - the
three things that decide what any button here will actually do.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDialog, QFrame, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from . import commands, core as core_mod, theme, widgets
from .runner import LauncherProcess, RunState
from .settings import Settings
from .pages.artifacts import ArtifactsPage
from .pages.command import CommandPage
from .pages.credentials import CredentialsPage
from .pages.environments import EnvironmentsPage
from .pages.log import LogPage
from .pages.run import RunPage
from .pages.settings_dialog import SettingsDialog

CONFIGURE = [("environments", "Environments", "▤"),
             ("credentials", "Credentials", "◍"),
             ("commands", "Command", "⌗")]
OBSERVE = [("run", "Run", "▶"), ("log", "Log", "≡"), ("artifacts", "Artifacts", "◫")]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("chrome-multi-session — GUI")
        self.resize(1380, 880)

        self.settings = Settings()
        self.core = core_mod.Core(self.settings.core_script, self.settings.interpreter,
                                  self.settings.config)
        self.inventory = core_mod.Inventory()
        self.run_state = RunState(self)
        self.process = LauncherProcess(self)

        self._nav_buttons = {}
        self._build_menu()
        self._build_ui()
        self._connect_process()

        self.show_page(self.settings.page)
        # Ask the core what exists as soon as the window is up, so the pages are
        # populated before the user reaches them.
        QTimer.singleShot(80, self.refresh_inventory)

    # -- construction ---------------------------------------------------------
    def _build_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        reload_action = QAction("&Reload users.json", self)
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        reload_action.triggered.connect(lambda: self.credentials.load(self.core.config_path))
        file_menu.addAction(reload_action)
        save_action = QAction("&Save users.json", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(lambda: self.credentials.save())
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        settings_action = QAction("Se&ttings…", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        run_menu = menu.addMenu("&Run")
        self.run_action = QAction("&Run command", self)
        self.run_action.setShortcut(QKeySequence("Ctrl+Return"))
        self.run_action.triggered.connect(self.start_run)
        run_menu.addAction(self.run_action)
        self.stop_action = QAction("&Stop", self)
        self.stop_action.setShortcut(QKeySequence("Ctrl+."))
        self.stop_action.triggered.connect(self.stop_run)
        self.stop_action.setEnabled(False)
        run_menu.addAction(self.stop_action)

        tools_menu = menu.addMenu("&Tools")
        refresh_action = QAction("Refresh --describe", self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(self.refresh_inventory)
        tools_menu.addAction(refresh_action)
        tools_menu.addSeparator()
        for label, args in commands.ONE_SHOTS:
            action = QAction(label, self)
            action.triggered.connect(lambda _c=False, a=args, l=label: self.one_shot(l, a))
            tools_menu.addAction(action)
        tools_menu.addSeparator()
        init_action = QAction("Create a starter users.json", self)
        init_action.triggered.connect(lambda: self.one_shot("--init-users-json",
                                                            ["--init-users-json"]))
        tools_menu.addAction(init_action)

        help_menu = menu.addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.about)
        help_menu.addAction(about_action)

    def _build_ui(self):
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        # --- toolbar ---------------------------------------------------------
        bar = QFrame()
        bar.setProperty("role", "bar")
        bar_layout = QVBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        self.run_button = QPushButton("▶ RUN")
        self.run_button.setProperty("variant", "primary")
        self.run_button.clicked.connect(self.start_run)
        self.stop_button = QPushButton("■ Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_run)
        self.copy_button = QPushButton("⧉ Copy command")
        self.copy_button.clicked.connect(lambda: self.command.copy_command())
        self.refresh_button = QPushButton("↻ Refresh describe")
        self.refresh_button.clicked.connect(self.refresh_inventory)
        self.describe_label = widgets.mono("")
        settings_button = QPushButton("⚙ Settings")
        settings_button.clicked.connect(self.open_settings)
        bar_layout.addWidget(widgets.row(
            self.run_button, self.stop_button, widgets.vline(), self.copy_button,
            self.refresh_button, None, self.describe_label, settings_button))
        column.addWidget(bar)

        # --- body: rail + pages ---------------------------------------------
        body = QWidget()
        body_row = QVBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        from PySide6.QtWidgets import QHBoxLayout
        inner = QHBoxLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        rail = QFrame()
        rail.setProperty("role", "sidebar")
        rail.setFixedWidth(206)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 8, 0, 8)
        rail_layout.setSpacing(0)
        for title, entries in (("Configure", CONFIGURE), ("Observe", OBSERVE)):
            heading = widgets.kicker(title)
            heading.setContentsMargins(14, 8, 14, 6)
            rail_layout.addWidget(heading)
            for key, label, glyph in entries:
                button = QPushButton("%s   %s" % (glyph, label))
                button.setProperty("variant", "nav")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _c=False, k=key: self.show_page(k))
                self._nav_buttons[key] = button
                rail_layout.addWidget(button)
            rail_layout.addSpacing(10)
        rail_layout.addStretch(1)
        self.rail_note = QLabel("")
        self.rail_note.setStyleSheet(
            "font-family: %s; font-size: 10px; color: %s; border-top: 1px solid %s;"
            "padding: 8px 14px;" % (theme.MONO_CSS, theme.NEUTRAL[600], theme.DIVIDER))
        self.rail_note.setWordWrap(True)
        rail_layout.addWidget(self.rail_note)
        inner.addWidget(rail)

        self.stack = QStackedWidget()
        self.environments = EnvironmentsPage(self.settings)
        self.credentials = CredentialsPage()
        self.command = CommandPage(self.settings)
        self.run = RunPage(self.run_state)
        self.log = LogPage()
        self.artifacts = ArtifactsPage()
        self._pages = {"environments": self.environments, "credentials": self.credentials,
                       "commands": self.command, "run": self.run, "log": self.log,
                       "artifacts": self.artifacts}
        for page in self._pages.values():
            self.stack.addWidget(page)
        inner.addWidget(self.stack, 1)
        body_row.addLayout(inner)
        column.addWidget(body, 1)

        self.command.run_requested.connect(self.start_run)
        self.credentials.saved.connect(self.refresh_inventory)
        self.environments.directories_changed.connect(
            lambda: self.command.set_inventory(self.inventory))
        self.run_state.run_dir_known.connect(self.artifacts.set_run_dir)
        self.run_state.artifacts_written.connect(self.artifacts.note_artifacts)

        self.setCentralWidget(central)

        # --- status bar ------------------------------------------------------
        self.status_core = QLabel("")
        self.status_python = QLabel("")
        self.status_config = QLabel("")
        self.status_state = QLabel("idle")
        for label in (self.status_core, self.status_python, self.status_config,
                      self.status_state):
            label.setStyleSheet("font-family: %s; font-size: 11px; color: %s;"
                                % (theme.MONO_CSS, theme.NEUTRAL[700]))
            self.statusBar().addWidget(label)
        self.status_right = QLabel("ready")
        self.status_right.setStyleSheet("font-family: %s; font-size: 11px; color: %s;"
                                        % (theme.MONO_CSS, theme.NEUTRAL[600]))
        self.statusBar().addPermanentWidget(self.status_right)
        self._update_status()

    def _connect_process(self):
        self.process.event.connect(self._on_event)
        self.process.log.connect(self.log.append)
        self.process.started.connect(self._on_started)
        self.process.finished.connect(self._on_finished)
        self.process.failed.connect(self._on_failed)

    # -- navigation -----------------------------------------------------------
    def show_page(self, key):
        page = self._pages.get(key)
        if page is None:
            key, page = "commands", self.command
        self.stack.setCurrentWidget(page)
        for name, button in self._nav_buttons.items():
            button.setProperty("active", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        self.settings.page = key

    # -- core -----------------------------------------------------------------
    def refresh_inventory(self):
        self.command.set_core(self.core)
        if not self.core.is_configured():
            self.describe_label.setText("core not configured")
            self._update_status()
            return
        self.describe_label.setText("reading --describe …")
        try:
            payload = self.core.describe()
        except core_mod.CoreError as exc:
            self.describe_label.setText("--describe failed")
            self.status_right.setText("describe failed")
            QMessageBox.warning(self, "Cannot read the core",
                                "%s\n\nCheck Settings → Core script / Interpreter."
                                % exc)
            return
        self.inventory = core_mod.Inventory(payload)
        self.environments.set_inventory(self.inventory)
        self.command.set_inventory(self.inventory)
        self.credentials.load(self.inventory.config_path or self.core.config_path)
        self.describe_label.setText(self.inventory.summary())
        self.rail_note.setText("core %s\nPySide6 %s" % (self.inventory.version,
                                                        _pyside_version()))
        if self.inventory.warnings:
            self.status_right.setText(self.inventory.warnings[0][:90])
        self._update_status()

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply()
            self.core = core_mod.Core(self.settings.core_script,
                                      self.settings.interpreter, self.settings.config)
            self.refresh_inventory()

    def one_shot(self, title, args):
        """Run a command that just prints something, and show what it printed."""
        if not self.core.is_configured():
            QMessageBox.information(self, title, "Configure the core first "
                                                 "(Settings → Core script).")
            return
        try:
            code, out, err = self.core.run(*args)
        except core_mod.CoreError as exc:
            QMessageBox.warning(self, title, str(exc))
            return
        box = QMessageBox(self)
        box.setWindowTitle("%s (exit %d)" % (title, code))
        box.setText(title)
        box.setDetailedText((out or "") + ("\n" + err if err else ""))
        box.setStyleSheet("QLabel { font-family: %s; }" % theme.MONO_CSS)
        box.exec()
        if args == ["--init-users-json"]:
            self.refresh_inventory()

    # -- running --------------------------------------------------------------
    def start_run(self):
        if self.process.is_running():
            return
        if not self.core.is_configured():
            QMessageBox.information(self, "Run", "Configure the core first "
                                                 "(Settings → Core script).")
            return
        args = self.command.argv()
        try:
            argv = self.core.argv(*args)
        except core_mod.CoreError as exc:
            QMessageBox.warning(self, "Run", str(exc))
            return
        self.run_state.reset()
        self.log.clear()
        state = self.command.state()
        self.run.run_started("jobs=%s · %s · events on stdout"
                             % (state.get("--jobs") or "1",
                                state.get("--run-tests") or "(launch only)"))
        self.show_page("run")
        self.process.start(argv, working_dir=self.core.root or None)

    def stop_run(self):
        if not self.process.is_running():
            return
        self.status_right.setText("stopping — the launcher closes its windows first")
        self.process.stop()

    def _on_started(self, argv):
        self._set_running(True)
        self.log.append({"ts": "", "level": "INFO", "session": "",
                         "text": "$ " + " ".join(argv)})

    def _on_event(self, event):
        self.run_state.handle(event)

    def _on_finished(self, code):
        self._set_running(False)
        self.run.run_finished(code)
        self.status_right.setText("finished (exit %d)" % code)
        if self.run_state.run_dir:
            self.artifacts.set_run_dir(self.run_state.run_dir)

    def _on_failed(self, message):
        self._set_running(False)
        QMessageBox.warning(self, "Run", message)

    def _set_running(self, running):
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.run_action.setEnabled(not running)
        self.stop_action.setEnabled(running)
        self.command.set_running(running)
        self.status_state.setText("state: running" if running else "state: idle")
        if running:
            self.status_right.setText("events: stdout · logs: stderr")

    # -- chrome ---------------------------------------------------------------
    def _update_status(self):
        self.status_core.setText("core: %s" % (self.core.script or "—"))
        self.status_python.setText("python: %s" % (self.core.interpreter or "—"))
        self.status_config.setText("config: %s"
                                   % (os.path.basename(self.core.config_path) or "—"))

    def about(self):
        QMessageBox.about(
            self, "chrome-multi-session GUI",
            "A front-end for session_launcher.py.\n\n"
            "The GUI never imports the core: it spawns the launcher through the "
            "configured interpreter, reads --describe for what exists, and follows "
            "--events=- for what happens.\n\ncore: %s\nPySide6: %s"
            % (self.inventory.version or "not detected", _pyside_version()))

    def closeEvent(self, event):
        if self.process.is_running():
            answer = QMessageBox.question(
                self, "A run is in progress",
                "The launcher is still running. Stop it and close?\n\n"
                "Stopping lets it shut the Chrome windows down gracefully.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.process.stop()
            self.process._proc.waitForFinished(8000)
        self.settings.save_geometry(self.saveGeometry())
        super().closeEvent(event)


def _pyside_version():
    try:
        import PySide6
        return PySide6.__version__
    except Exception:
        return "?"
