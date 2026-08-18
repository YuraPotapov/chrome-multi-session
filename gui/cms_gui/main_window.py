"""The main window: toolbar, CONFIGURE/OBSERVE sidebar, pages, status bar.

Layout follows the design export: a run/stop toolbar across the top, a narrow
navigation rail split into what you *configure* and what you *observe*, and a
status bar that always says which core, interpreter and config are in play - the
three things that decide what any button here will actually do.

Two pages can start a run - Launch Sessions for a user, Command for a developer -
so this window is also the place that decides which of them the toolbar's RUN
acts on, and the place that records the result. Both concerns live here rather
than in either page, so neither page has to know the other exists.
"""

import logging
import os
import platform

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QInputDialog, QLabel,
                               QMainWindow, QMenu,
                               QMessageBox, QPushButton, QStackedWidget,
                               QToolButton, QVBoxLayout, QWidget)

from . import (commands, core as core_mod, history as history_mod, icon,
               launch as launch_mod, load as load_mod, theme, widgets)
from .loader import LoaderThread
from . import version as gui_version
from .runner import LauncherProcess, RunState
from .settings import Settings
from .pages.artifacts import ArtifactsPage
from .pages.command import CommandPage
from .pages.credentials import CredentialsPage
from .pages.environments import EnvironmentsPage
from .pages.history import HistoryPage
from .pages.launch import LaunchSessionsPage
from .pages.log import LogPage
from .pages.run import RunPage
from .pages.scenarios import ScenariosPage
from .pages.settings_dialog import SettingsDialog

# (page key, label, glyph name resolved by theme.glyph at build time)
CONFIGURE = [("environments", "Environments", "environments"),
             ("credentials", "Credentials", "credentials"),
             ("scenarios", "Scenarios", "scenarios"),
             ("commands", "Command", "command"),
             ("launch", "Launch Sessions", "launch")]
OBSERVE = [("run", "Run", "run"), ("log", "Log", "log"),
           ("artifacts", "Artifacts", "artifacts"),
           ("history", "History", "history")]

# Pages only a developer needs. Launch Sessions is never in here: it is the
# primary interface in one mode and still available in the other.
DEVELOPER_ONLY = ("commands",)

# The rail's width is measured from its contents (see _build_ui); these only set
# the floor, the left/right inset of the group headings, and enough slack that a
# label never sits flush against the divider.
RAIL_MIN_WIDTH = 196
RAIL_PADDING = 14
RAIL_SLACK = 18

# Taken off a group heading's measured width. "CONFIGURE" is letter-spaced, and the
# 2px lands after the final character as well as between characters, so its
# reported width ends in space that draws nothing. A heading should not widen the
# rail on account of that.
HEADING_TRIM = 10

# How long to wait for the launcher to stop when the window is closed on top of a
# live run. The launcher SIGTERMs every window and gives Chrome up to 15 s each to
# flush its cookies, so anything shorter risks closing on a login mid-write.
CLOSE_WAIT_MS = 20000

# The splash. READY is how long "Ready!" stays up once the pages are populated -
# long enough to read rather than a flicker. MAX is the hard stop: --describe is
# a subprocess that can hang (a busy machine, a core that never answers), and a
# splash with no window behind it is an app that looks dead. It always goes.
SPLASH_READY_MS = 800
SPLASH_MAX_MS = 15000


def _run_label(text):
    """RUN. The menu's arrow is drawn by Qt in the button's own arrow half."""
    return theme.labelled("run", text)


def _close_warning(windows):
    """What the close-during-a-run warning says, for ``windows`` open windows.

    Its own function so the wording can be tested without standing up a dialog.
    """
    closes = (" and closes %d window%s" % (windows, "" if windows == 1 else "s")
              if windows else "")
    return ("Closing stops the run first%s, so each window shuts down gracefully "
            "and keeps its login. This can take a few seconds." % closes)


class MainWindow(QMainWindow):
    def __init__(self, splash=None, auto_launch=None, headless=False):
        super().__init__()
        self.splash = splash
        self._auto_launch = auto_launch
        self._headless = headless
        self._closing = False
        self.setWindowTitle("chrome-multi-session — GUI")
        self.setWindowIcon(icon.app_icon())
        self.resize(1380, 880)

        self.settings = Settings()
        self.core = core_mod.Core(self.settings.core_script, self.settings.interpreter,
                                  self.settings.config)
        self.inventory = core_mod.Inventory()
        self.run_state = RunState(self)
        self.process = LauncherProcess(self)
        self.history = history_mod.History(parent=self)

        self._nav_buttons = {}
        self._run_source = self.settings.run_source
        self._entry_id = None
        self._stopping = False
        self._chrome_warned = False
        self._build_menu()
        self._build_ui()
        self._connect_process()

        self.set_developer_mode(self.settings.developer_mode)
        if self.settings.always_on_top:
            self.set_always_on_top(True)
        self.show_page(self.settings.page)
        
        # Two labels rather than one string: the machine is read on a timer, the
        # worker count arrives on the event stream, and neither should have to
        # wait for the other's cadence to show what it knows.
        self.workers_label = QLabel("")
        self.workers_label.setStyleSheet("padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.workers_label)
        self.run_state.changed.connect(self._update_workers)
        self.metrics_label = QLabel("")
        self.metrics_label.setStyleSheet("padding: 0 10px;")
        self.statusBar().addPermanentWidget(self.metrics_label)

        self.loader_thread = None
        self._load = load_mod.Sampler()
        self._metrics_timer = QTimer(self)
        self._metrics_timer.timeout.connect(self._update_metrics)
        self._metrics_timer.start(2000)
        
        # Ask the core what exists as soon as the window is up, so the pages are
        # populated before the user reaches them.
        QTimer.singleShot(500 if self.splash else 80, self.refresh_inventory)
        if self.splash:
            QTimer.singleShot(SPLASH_MAX_MS, self._finish_splash)

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

        view_menu = menu.addMenu("&View")
        self.developer_action = QAction("&Developer mode", self)
        self.developer_action.setCheckable(True)
        self.developer_action.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.developer_action.toggled.connect(self.set_developer_mode)
        view_menu.addAction(self.developer_action)
        
        self.always_on_top_action = QAction("Always on Top", self)
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.toggled.connect(self.set_always_on_top)
        view_menu.addAction(self.always_on_top_action)
        
        self.dark_mode_action = QAction("&Dark mode", self)
        self.dark_mode_action.setCheckable(True)
        self.dark_mode_action.setChecked(self.settings.dark_mode)
        self.dark_mode_action.toggled.connect(self.set_dark_mode)
        view_menu.addAction(self.dark_mode_action)

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
        # A tool button rather than a plain one: RUN is the ordinary case and
        # stays one click, while recording is the same launch with the recorder
        # available - a mode of running, not a separate thing to go and find.
        self.run_button = QToolButton()
        self.run_button.setText(_run_label("RUN"))
        self.run_button.setProperty("variant", "primary")
        self.run_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.run_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.run_button.setProperty("hasmenu", "true")   # reserves the arrow's half
        self.run_button.clicked.connect(self.start_run)
        self.run_menu = QMenu(self.run_button)
        # One entry, because there is only one thing to want: record. Whether
        # that continues a scenario or starts a new one follows from what is
        # selected, and is confirmed rather than guessed.
        self.record_action = QAction("With Recorder", self)
        self.record_action.setToolTip(
            "Open the windows with the Scenario Recorder shown in each of them. "
            "Continues the selected scenario if there is one.")
        self.record_action.triggered.connect(self.start_recording)
        self.run_menu.addAction(self.record_action)
        self.run_button.setMenu(self.run_menu)
        # Stop mirrors RUN: the button is the whole run, the arrow is the one
        # window you are actually looking at. Same shape, because "stop" reads as
        # one idea with a narrower version of itself inside it.
        self.stop_button = QToolButton()
        self.stop_button.setText(theme.labelled("stop", "Stop"))
        self.stop_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.stop_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.stop_button.setProperty("hasmenu", "true")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_run)
        self.stop_menu = QMenu(self.stop_button)
        # Filled from the live run: which windows exist is not knowable until one
        # is running, and stale entries would offer to stop what is already gone.
        self.stop_menu.aboutToShow.connect(self._fill_stop_menu)
        self.stop_button.setMenu(self.stop_menu)
        self.copy_button = QPushButton(theme.labelled("copy", "Copy command"))
        self.copy_button.clicked.connect(lambda: self.command.copy_command())
        self.refresh_button = QPushButton(theme.labelled("refresh", "Refresh"))
        self.refresh_button.clicked.connect(self.refresh_inventory)
        self.describe_label = widgets.mono("")
        # A checkable button rather than a menu item alone: which mode you are in
        # decides what the whole window offers, so it has to be readable at a
        # glance instead of being remembered.
        self.developer_button = QPushButton("")
        self.developer_button.setCheckable(True)
        self.developer_button.setCursor(Qt.PointingHandCursor)
        self.developer_button.toggled.connect(self.set_developer_mode)
        settings_button = QPushButton(theme.labelled("settings", "Settings"))
        settings_button.clicked.connect(self.open_settings)
        bar_layout.addWidget(widgets.row(
            self.run_button, self.stop_button, widgets.vline(), self.copy_button,
            self.refresh_button, None, self.describe_label, widgets.vline(),
            self.developer_button, settings_button))
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
        rail_layout = QVBoxLayout(rail)
        # 1px reserved on the right for the frame's own border. A stylesheet border
        # is painted inside the widget's rect and children are not clipped to the
        # contents rect, so a full-width child with any fill of its own paints over
        # it - which is how the divider ended up broken at the group headings and at
        # whichever nav item was active.
        rail_layout.setContentsMargins(0, 8, 1, 8)
        rail_layout.setSpacing(0)
        # The rail is sized from what is actually in it. A fixed width was fine
        # while the labels were short and the font was whatever this machine had,
        # but "Launch Sessions" and a letter-spaced "CONFIGURE" overflow it as soon
        # as the body font is wider - a condensed family missing, a larger system
        # font, display scaling - and a QFrame with a fixed width clips rather than
        # growing. So every label is measured and the widest one decides.
        widest = 0
        for title, entries in (("Configure", CONFIGURE), ("Observe", OBSERVE)):
            heading = widgets.kicker(title)
            heading.setContentsMargins(RAIL_PADDING, 8, RAIL_PADDING, 6)
            widest = max(widest, heading.sizeHint().width() - HEADING_TRIM)
            rail_layout.addWidget(heading)
            for key, label, glyph in entries:
                button = QPushButton("%s   %s" % (theme.glyph(glyph), label))
                button.setProperty("variant", "nav")
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _c=False, k=key: self.show_page(k))
                self._nav_buttons[key] = button
                widest = max(widest, button.sizeHint().width())
                rail_layout.addWidget(button)
            rail_layout.addSpacing(10)
        rail_layout.addStretch(1)
        rail.setFixedWidth(max(RAIL_MIN_WIDTH, widest + RAIL_SLACK))
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
        self.scenarios = ScenariosPage(self.settings)
        self.command = CommandPage(self.settings)
        self.launch = LaunchSessionsPage(self.settings)
        self.run = RunPage(self.run_state)
        self.log = LogPage(self.settings)
        self.artifacts = ArtifactsPage(self.settings)
        self.history_page = HistoryPage(self.history)
        # Both observing pages read the run record for what to offer, so they open
        # on the last run rather than on nothing after a restart.
        self.log.set_history(self.history)
        self.artifacts.set_history(self.history)
        self._pages = {"environments": self.environments, "credentials": self.credentials,
                       "scenarios": self.scenarios,
                       "commands": self.command, "launch": self.launch,
                       "run": self.run, "log": self.log,
                       "artifacts": self.artifacts, "history": self.history_page}
        for page in self._pages.values():
            self.stack.addWidget(page)
        inner.addWidget(self.stack, 1)
        body_row.addLayout(inner)
        column.addWidget(body, 1)

        self.command.run_requested.connect(lambda: self.start_run("commands"))
        self.launch.run_requested.connect(lambda: self.start_run("launch"))
        self.credentials.saved.connect(self.refresh_inventory)
        # Writing a scenario changes what --run-tests can run, so the inventory
        # every other page reads has to be re-read.
        self.scenarios.saved.connect(self.refresh_inventory)
        self.environments.directories_changed.connect(self._directories_changed)
        self.run_state.run_dir_known.connect(self.artifacts.set_run_dir)
        self.run_state.artifacts_written.connect(self.artifacts.note_artifacts)
        self.history_page.rerun_requested.connect(self.rerun_entry)
        self.history_page.restore_requested.connect(self.restore_entry)
        self.history_page.open_log_requested.connect(self._open_log)
        self.history_page.open_artifacts_requested.connect(self._open_artifacts)

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
        if key in DEVELOPER_ONLY and not self.settings.developer_mode:
            key = "launch"
        page = self._pages.get(key)
        if page is None:
            key, page = "launch", self.launch
        self.stack.setCurrentWidget(page)
        for name, button in self._nav_buttons.items():
            button.setProperty("active", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        self.settings.page = key
        # RUN follows whichever of the two launching pages you were last in, so
        # the toolbar button and Ctrl+Return never act on a page you left behind.
        if key in ("launch", "commands"):
            self._run_source = key
            self.settings.run_source = key
            self._update_run_label()

    def set_always_on_top(self, enabled):
        enabled = bool(enabled)
        self.settings.always_on_top = enabled
        self.always_on_top_action.blockSignals(True)
        self.always_on_top_action.setChecked(enabled)
        self.always_on_top_action.blockSignals(False)
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        # Changing a flag on a mapped window makes Qt destroy and re-create it,
        # so it has to be shown again or it simply disappears - but ONLY if it
        # was on screen to begin with. At startup this runs while the splash is
        # still up and the pages are empty; showing here put a half-built window
        # on screen and left the data arriving a second or two later.
        if was_visible:
            self.show()
            if enabled:
                self.raise_()
                self.activateWindow()

    def _finish_splash(self):
        """Take the splash down and show the window. Safe to call twice.

        Reached three ways: the load finishing, the load failing, and the hard
        timeout armed at startup - whichever comes first wins, and the rest are
        no-ops.
        """
        if not self.splash:
            return
        if not self._headless:
            self.show()
        self.splash.finish(self)
        self.splash = None

    def _keep_on_top(self):
        """Restack above a window that has just mapped, if asked to stay on top.

        A run opens seven browsers, each mapping and raising itself. The window
        manager honours the always-on-top state, but re-asserting costs nothing
        and covers the window that maps in the same instant the flag is applied.

        raise_() only, never activateWindow(): the auto-login extension is typing
        into one of those browsers, and taking keyboard focus off it mid-login is
        how a launch loses its password.
        """
        if self.always_on_top_action.isChecked():
            self.raise_()

    def _update_workers(self):
        """Show the number of sessions in force right now, and what last set it.

        Shown for every parallel run, not only the governed ones: "how many are
        running" is a fair question whether or not the answer can change, and it
        is the launch page's number confirmed rather than repeated - that one is
        what was asked for, this one is what the run is doing.
        """
        workers = self.run_state.workers
        if not workers or not self.process.is_running():
            self.workers_label.clear()
            self.workers_label.setToolTip("")
            return
        limit, ceiling = workers["limit"], workers["ceiling"]
        held_back = limit is not None and ceiling is not None and limit < ceiling
        self.workers_label.setText(
            "<span style='color: %s'>%s %s of %s</span>"
            % (theme.WARN if held_back else theme.NEUTRAL[600],
               theme.glyph("run"), limit, ceiling))
        self.workers_label.setToolTip(
            "%s %s running at once, out of %s.\nLast change: %s"
            % (limit, workers["unit"], ceiling, workers["why"] or "none yet"))

    def _update_metrics(self):
        load = self._load.read()
        used = load.used_percent
        if not load.readable:
            self.metrics_label.clear()
            self._metrics_timer.stop()   # this platform will not start answering
            return
        # Red for stall, not for a busy CPU: a saturated machine driving windows
        # is the tool working. Only memory pressure threatens the run.
        strained = (load.mem_stall is not None and load.mem_stall > 10) or \
                   (used is not None and used > 90)
        colour = theme.BAD if strained else theme.NEUTRAL[600]
        self.metrics_label.setText(
            "<span style='color: %s'>CPU %s &nbsp;RAM %s</span>"
            % (colour,
               "--" if load.cpu_percent is None else "%.0f%%" % load.cpu_percent,
               "--" if used is None else "%.0f%%" % used))

    def set_dark_mode(self, enabled):
        from PySide6.QtWidgets import QApplication
        enabled = bool(enabled)
        self.settings.dark_mode = enabled
        
        self.dark_mode_action.blockSignals(True)
        self.dark_mode_action.setChecked(enabled)
        self.dark_mode_action.blockSignals(False)
        
        theme.set_dark_mode(enabled)
        QApplication.instance().setStyleSheet(theme.stylesheet())

    def set_developer_mode(self, enabled):
        enabled = bool(enabled)
        self.settings.developer_mode = enabled
        # Both are checkable and both call back in here, so each has to be set
        # quietly or the two would bounce the change between them.
        for control in (self.developer_button, self.developer_action):
            control.blockSignals(True)
            control.setChecked(enabled)
            control.blockSignals(False)
        self.developer_button.setText(
            theme.labelled("developer", "Developer mode: on" if enabled
                           else "Developer mode: off"))
        self.developer_button.setProperty("variant", "primary" if enabled else "")
        self.developer_button.style().unpolish(self.developer_button)
        self.developer_button.style().polish(self.developer_button)

        for key in DEVELOPER_ONLY:
            nav = self._nav_buttons.get(key)
            if nav is not None:
                nav.setVisible(enabled)
        self.copy_button.setVisible(enabled)
        self.launch.set_developer_mode(enabled)
        if not enabled and self.settings.page in DEVELOPER_ONLY:
            self.show_page("launch")
        else:
            self._update_run_label()

    def _update_run_label(self):
        """Say which page RUN will act on, but only when both are reachable."""
        if self.settings.developer_mode and self._run_source == "commands":
            self.run_button.setText(_run_label("RUN COMMAND"))
        else:
            self.run_button.setText(_run_label("RUN"))

    def _run_page(self, source=None):
        source = source or self._run_source
        if source == "commands" and self.settings.developer_mode:
            return "commands", self.command
        return "launch", self.launch

    def _directories_changed(self):
        self.command.set_inventory(self.inventory)
        self.launch.set_inventory(self.inventory)

    # -- core -----------------------------------------------------------------
    def refresh_inventory(self):
        # The first refresh is a delayed singleShot, so a window closed inside
        # that delay would otherwise start a describe on its way out - a thread
        # nobody is left to wait for, and widgets nobody is left to fill in.
        if self._closing:
            return
        if self.loader_thread and self.loader_thread.isRunning():
            return

        self.command.set_core(self.core)
        self.launch.set_core(self.core)
        # Scenarios reads and writes files through the core too, not just runs it.
        self.scenarios.set_core(self.core)
        
        def _splash_msg(text):
            """Progress, for the splash only.

            Deliberately not the top bar's describe label: stage names like
            "Parsing inventory..." are this window talking to itself, and one
            left showing after a slow load reads as a state the app is stuck in.
            That label says one quiet thing while loading, then the summary.
            """
            if self.splash:
                # One line; the splash draws it into its own status strip, so
                # neither the padding nor the colour belongs here any more.
                self.splash.showMessage("%s  \u00B7  %s" % (gui_version(), text))
                self.splash.repaint()
                QApplication.processEvents()

        def _on_error(exc):
            self.describe_label.setText("could not read the configuration")
            self.status_right.setText("check Settings -> Core script")
            self._finish_splash()
            if self._headless:
                logging.error("Cannot read the core: %s", exc)
                QApplication.quit()
                return
            QMessageBox.warning(self, "Cannot read the core",
                                "%s\n\nCheck Settings -> Core script / Interpreter."
                                % exc)
                                
        def _on_finished(inventory):
            self.inventory = inventory
            
            _splash_msg("Populating environments...")
            self.environments.set_inventory(self.inventory)
            
            _splash_msg("Populating commands...")
            self.command.set_inventory(self.inventory)
            
            _splash_msg("Populating launchers...")
            self.launch.set_inventory(self.inventory)
            
            _splash_msg("Populating scenarios...")
            self.scenarios.set_inventory(self.inventory)
            
            _splash_msg("Loading credentials...")
            self.credentials.load(self.inventory.config_path or self.core.config_path)
            
            _splash_msg("Finalizing UI...")
            self.describe_label.setText(self.inventory.summary())
            self.rail_note.setText("core %s\nPySide6 %s" % (self.inventory.version,
                                                            _pyside_version()))
            if self.inventory.warnings:
                self.status_right.setText(self.inventory.warnings[0][:90])
            self._update_status()
            self._warn_about_chrome()
            
            if self.splash:
                _splash_msg("Ready!")
                QTimer.singleShot(SPLASH_READY_MS, self._finish_splash)
            else:
                self._finish_splash()
                
            if self._auto_launch:
                self.show_page("launch")
                self.launch._reload_configs(select=self._auto_launch)
                QTimer.singleShot(100, self.launch.run_requested.emit)
                self._auto_launch = None
                
        self.describe_label.setText("reading the configuration …")
        self.loader_thread = LoaderThread(self.core, self)
        self.loader_thread.progress.connect(_splash_msg)
        self.loader_thread.error.connect(_on_error)
        self.loader_thread.finished_inventory.connect(_on_finished)
        self.loader_thread.start()

    def _warn_about_chrome(self):
        """Say so, once, when the core cannot find a browser to launch.

        Everything this app does ends in a Chrome window, so learning at startup
        beats learning halfway through a launch. Shown once per session: it is a
        prerequisite the user has to go away and fix, not a recurring alert.
        """
        problem = self.inventory.chrome_problem()
        if not problem:
            self._chrome_warned = False
            return
        self.status_right.setText("Google Chrome not found")
        if getattr(self, "_chrome_warned", False):
            return
        self._chrome_warned = True
        QMessageBox.warning(self, "Google Chrome is required", problem)

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
                                                 "(Settings -> Core script).")
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
    def recordable_scenario(self):
        """The scenario a recording would continue, or "".

        Whatever is open in the Scenarios editor comes first - that is the one
        being worked on. Otherwise a single scenario chosen on Launch Sessions,
        since picking exactly one and then recording plainly means that one. Only
        ever something writable: a bundled scenario cannot be recorded into.
        """
        current = self.scenarios.current
        if current and current.get("writable"):
            return current["id"]
        chosen = [s for s in self.launch.state().get("scenarios", {}).get("selected", [])
                  if not str(s).startswith("tag:")]
        if len(chosen) == 1:
            row = self.inventory.scenario(chosen[0])
            if row.get("writable"):
                return chosen[0]
        return ""

    def ask_recording_target(self):
        """Decide what a recording writes to: ("continue", id) / ("new", "") / cancel.

        With nothing selected there is nothing to ask about. With something
        selected, asking is the point: appending to a scenario and replacing one
        look identical until it is too late, and the answer is a click either way.

        Split from the launch so the decision can be tested without a dialog.
        """
        scenario = self.recordable_scenario()
        if not scenario:
            return "new", ""
        box = QMessageBox(self)
        box.setWindowTitle("Record")
        box.setIcon(QMessageBox.Question)
        box.setText("\"%s\" is selected." % scenario)
        box.setInformativeText(
            "Continue recording into it - its steps are loaded and what you "
            "capture is added to them - or start a new scenario?")
        keep = box.addButton("Continue \"%s\"" % scenario, QMessageBox.AcceptRole)
        fresh = box.addButton("Start new", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(keep)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep:
            return "continue", scenario
        if clicked is fresh:
            return "new", ""
        return "cancel", ""

    def start_recording(self):
        """Launch with the recorder available, from whichever page RUN would use.

        Deliberately the same launch: same environment, same accounts, same
        profiles. The only difference is that the windows carry a debug port and
        show the recorder, and that nothing is run afterwards - the launcher
        stays up for as long as you are recording.
        """
        login = self.ask_recording_account()
        if login == "cancel":
            return
        choice, scenario = self.ask_recording_target()
        if choice == "cancel":
            return
        self.start_run(recorder=scenario or True, only_login=login or None)

    def ask_recording_account(self):
        """Which single account to record: a login, "" (leave as configured), or "cancel".

        Recording is a one-window activity - you can only be clicking in one of
        them - and the core refuses more than one outright. Asking here turns
        that refusal into a choice, at the moment the choice is obvious.
        """
        source, page = self._run_page(None)
        if source != "launch" or getattr(page, "inventory", None) is None:
            return ""       # the Command page states its own accounts; let the core judge
        logins = launch_mod.selected_logins(page.state(), page.inventory)
        if len(logins) <= 1:
            return ""
        # A list, not a row of buttons: eleven accounts is a normal number here.
        chosen, ok = QInputDialog.getItem(
            self, "Record",
            "Recording opens one window, and %d accounts are selected.\n"
            "Which one do you want to record?" % len(logins),
            logins, 0, False)
        if not ok or not chosen:
            return "cancel"
        return chosen

    def start_run(self, source=None, recorder=False, only_login=None):
        if self.process.is_running():
            return
        if not self.core.is_configured():
            QMessageBox.information(self, "Run", "Configure the core first "
                                                 "(Settings -> Core script).")
            return
        source, page = self._run_page(source)
        if source == "launch":
            problems = page.problem_list()
            if problems:
                if self._headless:
                    logging.error("Cannot launch, problems: %s", problems)
                    QApplication.quit()
                    return
                QMessageBox.information(self, "Launch Sessions",
                                        "Fix this first:\n\n- %s"
                                        % "\n- ".join(problems))
                return
        args = page.argv()
        if recorder:
            args = commands.for_recording(args)
            if only_login:
                # Replace rather than add: the configuration's own account list is
                # what made this ambiguous, and the core takes one --filter-users.
                args = [a for a in args if not a.startswith("--filter-users=")]
                args.insert(0, "--filter-users=%s" % only_login)
            # Ahead of --events, which build_argv always puts last.
            args.insert(0, "--recorder" if recorder is True
                        else "--recorder=%s" % recorder)
        try:
            argv = self.core.argv(*args)
        except core_mod.CoreError as exc:
            if self._headless:
                logging.error("CoreError: %s", exc)
                QApplication.quit()
                return
            QMessageBox.warning(self, "Run", str(exc))
            return
        self.run_state.reset()
        self.log.clear()
        self._stopping = False
        self._entry_id = self.history.begin(
            *self._history_payload(source, page, args, argv))
        if recorder:
            self.run.run_started(
                ("continuing %s · " % recorder if recorder is not True else "")
                + "recorder · press Capture Step (or F2) in a window")
        elif source == "launch":
            self.run.run_started("%s · events on stdout" % page.run_meta())
        else:
            state = page.state()
            self.run.run_started("jobs=%s · %s · events on stdout"
                                 % (state.get("--jobs") or "1",
                                    state.get("--run-tests") or "(launch only)"))
        self.show_page("run")
        self.process.start(argv, working_dir=self.core.root or None)

    def _history_payload(self, source, page, args, argv):
        """(kind, entry fields) for the run about to start.

        Both halves go in: what the user asked for, and the command it became.
        The first is what "run it again, but with one change" needs; the second is
        what makes the entry readable without the GUI at all.
        """
        try:
            display = self.core.display_argv(*args)
        except core_mod.CoreError:
            display = " ".join(argv)
        if source == "launch":
            config = page.state()
            return history_mod.LAUNCH, {
                "argv": list(argv), "display_command": display,
                "summary": page.describe_line(), "launch_config": config,
                "command_state": launch_mod.to_command_state(config, self.inventory)}
        state = page.state()
        return history_mod.COMMAND, {
            "argv": list(argv), "display_command": display,
            "summary": commands.preview(state, self.core), "command_state": state}

    def _fill_stop_menu(self):
        """One entry per window still running, plus everything.

        Rebuilt each time it opens rather than kept in step with the run: a menu
        that is only correct while it is on screen only has to be correct then.
        """
        self.stop_menu.clear()
        live = [s for s in self.run_state.ordered()
                if s["state"] in ("launching", "launched", "attached", "running")]
        for session in live:
            label = session["name"]
            if session.get("scenario"):
                label += "  (%s)" % session["scenario"]
            action = QAction(label, self)
            action.setToolTip("Stop this window and close it. The others carry on.")
            action.triggered.connect(
                lambda _checked=False, name=session["name"]: self.stop_session(name))
            self.stop_menu.addAction(action)
        if not live:
            nothing = QAction("No windows running", self)
            nothing.setEnabled(False)
            self.stop_menu.addAction(nothing)
            return
        self.stop_menu.addSeparator()
        everything = QAction("Stop everything", self)
        everything.triggered.connect(self.stop_run)
        self.stop_menu.addAction(everything)

    def stop_session(self, name):
        """Stop ONE window: it closes, the rest of the run carries on."""
        if not self.process.is_running():
            return
        if not self.process.send_command(command="stop-session", session=name):
            return
        self.run_state.mark_stopping(name)
        self.status_right.setText("stopping %s — the others carry on" % name)

    def stop_run(self):
        if not self.process.is_running():
            return

        window_count = len(self.run_state.sessions)
        msg = "Are you sure you want to stop the current run?"
        if window_count > 0:
            msg += f"\nThis will close {window_count} window{'s' if window_count != 1 else ''}."
            
        reply = QMessageBox.question(self, "Confirm Stop", msg,
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
            
        self._stopping = True
        self.status_right.setText("stopping — the launcher closes its windows first")
        self.process.stop()

    def _on_started(self, argv):
        self._set_running(True)
        self.log.append({"ts": "", "level": "INFO", "session": "",
                         "text": "$ " + " ".join(argv)})

    def _on_event(self, event):
        self.run_state.handle(event)
        kind = event.get("kind")
        if kind == "window.launched":
            self._keep_on_top()
        elif kind == "run.finished":
            # Settle the Run page now. The launcher may well still be up - with
            # the windows kept open it is - so Stop stays enabled and the run is
            # not marked over; only the page stops pretending steps are running.
            self.run.run_finished(event.get("exit_code") or 0)
            if self.process.is_running():
                self.status_right.setText(
                    "scenarios finished — windows open, Stop closes them")
        # A recording is scenario work, not run work, so it is shown where
        # scenarios live rather than in the run view.
        if str(event.get("kind", "")).startswith("recorder."):
            self.scenarios.handle_recorder_event(event)

    def _on_finished(self, code):
        self._set_running(False)
        self.run.run_finished(code)
        self.status_right.setText("finished (exit %d)" % code)
        if self.run_state.run_dir:
            self.artifacts.set_run_dir(self.run_state.run_dir)
        self._close_entry(history_mod.status_for(code, stopped=self._stopping),
                          exit_code=code)
        if self._headless:
            QApplication.quit()

    def _on_failed(self, message):
        self._set_running(False)
        self._close_entry(history_mod.ERROR, summary_suffix=message)
        if self._headless:
            logging.error("Run failed: %s", message)
            QApplication.quit()
            return
        QMessageBox.warning(self, "Run", message)

    def _close_entry(self, status, exit_code=None, summary_suffix=""):
        """Record how the run ended, and keep a copy of its log.

        The log has to be archived now: the next run clears the Log page, so this
        is the only moment the text still exists anywhere.
        """
        if self._entry_id is None:
            return
        summary = self.run_state.summary or {}
        passed, total = self.run_state.totals()
        entry_id, self._entry_id = self._entry_id, None
        log_path = self.history.log_path(entry_id)
        fields = {"status": status, "exit_code": exit_code,
                  "passed": int(summary.get("passed", passed) or 0),
                  "total": int(summary.get("total", total) or 0),
                  "run_dir": self.run_state.run_dir,
                  "log_file": log_path if self.log.write_to(log_path) else ""}
        if summary_suffix:
            existing = (self.history.entry(entry_id) or {}).get("summary", "")
            fields["summary"] = "%s — %s" % (existing, summary_suffix) if existing \
                else summary_suffix
        self.history.finish(entry_id, **fields)

    def _set_running(self, running):
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.run_action.setEnabled(not running)
        self.stop_action.setEnabled(running)
        self.command.set_running(running)
        self.launch.set_running(running)
        self.status_state.setText("state: running" if running else "state: idle")
        self._update_workers()   # the count is a live fact; it goes when the run does
        if running:
            self.status_right.setText("events: stdout · logs: stderr")

    # -- history --------------------------------------------------------------
    def restore_entry(self, entry):
        """Put a recorded run back into the page that produced it."""
        kind = entry.get("kind")
        if kind == history_mod.LAUNCH:
            self.launch.set_state(entry.get("launch_config") or {})
            self.show_page("launch")
            return "launch"
        # The Command page needs nothing new for this: set_state is its own API.
        if not self.settings.developer_mode:
            self.set_developer_mode(True)
        self.command.set_state(entry.get("command_state") or {})
        self.show_page("commands")
        return "commands"

    def rerun_entry(self, entry):
        source = self.restore_entry(entry)
        self.start_run(source)

    def _open_log(self, path):
        """Show an archived log on the Log page, where the filters are.

        Handing it to the desktop would open a text editor on something this app
        already renders better - level colouring, the session picker, search.
        """
        if not path or not os.path.isfile(path):
            return
        if self.log.show_file(path):
            self.show_page("log")
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_artifacts(self, run_dir):
        self.artifacts.show_dir(run_dir)
        self.show_page("artifacts")

    # -- chrome ---------------------------------------------------------------
    def _update_status(self):
        """Name the three things in play, without the full paths.

        These are absolute paths, and a status-bar QLabel is as wide as its text:
        spelling them out here set the window's minimum width to the length of the
        longest one. The file name says which, and the tooltip has the rest.
        """
        for label, prefix, path in ((self.status_core, "core", self.core.script),
                                    (self.status_python, "python",
                                     self.core.interpreter),
                                    (self.status_config, "config",
                                     self.core.config_path)):
            label.setText("%s: %s" % (prefix, os.path.basename(path or "") or "—"))
            label.setToolTip(path or "")

    def about(self):
        """Who both halves are, and where the core half was found.

        The GUI and the core are separate processes and can be separate builds -
        a checkout of one against an installed copy of the other is a normal
        thing to be running - so About names both versions rather than one, and
        says which file the core one came from.
        """
        QMessageBox.about(self, "chrome-multi-session GUI", self.about_text())

    def about_text(self):
        return ("A front-end for session_launcher.py.\n\n"
                "The GUI never imports the core: it spawns the launcher through "
                "the configured interpreter, reads --describe for what exists, "
                "and follows --events=- for what happens.\n\n"
                "GUI: %s\ncore: %s\nfrom: %s\n\nPySide6: %s\nPython: %s"
                % (gui_version(), self._core_version(),
                   self.core.script or "no core configured", _pyside_version(),
                   platform.python_version()))

    def _core_version(self):
        """What the core answers when asked what it is.

        --describe carries it, so normally this costs nothing. Before the first
        describe - or after one that failed - the core is asked directly rather
        than reporting "not detected" for a launcher that is sitting right there
        and working.
        """
        if self.inventory.version:
            return self.inventory.version
        try:
            banner = self.core.version()
        except Exception:
            return "not detected"
        # --version prints "chrome-multi-session <number>"; --describe carries
        # the number alone. Say the same thing either way.
        parts = (banner or "").split(None, 1)
        if len(parts) == 2 and not parts[0][:1].isdigit():
            return parts[1]
        return banner or "not detected"

    def _confirm_close_during_run(self):
        """Ask before closing on top of a live run. True means go ahead.

        A warning rather than a question, and the buttons say what they do:
        "Yes" and "No" leave it open which one closes without stopping, and that
        is the one outcome nobody wants - the launcher would be killed with its
        windows still up and their logins unflushed.
        """
        windows = len(self.run_state.sessions)
        box = QMessageBox(self)
        box.setWindowTitle("A run is in progress")
        box.setIcon(QMessageBox.Warning)
        box.setText("Autotests are still running.")
        box.setInformativeText(_close_warning(windows))
        stop = box.addButton("Stop all and close", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(stop)
        box.exec()
        return box.clickedButton() is stop

    def closeEvent(self, event):
        if self.process.is_running():
            if not self._confirm_close_during_run():
                event.ignore()
                return
            self.status_right.setText("stopping the run before closing…")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.process.stop()
                # Long enough for the launcher's own graceful teardown: it
                # SIGTERMs every window and gives Chrome up to 15 s each to flush
                # its cookies. Cutting that short is what loses a login.
                self.process._proc.waitForFinished(CLOSE_WAIT_MS)
            finally:
                QApplication.restoreOverrideCursor()
        self._closing = True
        self._metrics_timer.stop()
        self.settings.save_geometry(self.saveGeometry())
        # A QThread whose QObject is destroyed while it is still running takes the
        # process down with it, which turns closing the window mid-describe into a
        # crash on the way out. --describe is a subprocess we cannot cancel, so
        # wait for it rather than pretend it can be stopped.
        if self.loader_thread is not None and self.loader_thread.isRunning():
            self.loader_thread.wait(5000)
        super().closeEvent(event)


def _pyside_version():
    try:
        import PySide6
        return PySide6.__version__
    except Exception:
        return "?"
