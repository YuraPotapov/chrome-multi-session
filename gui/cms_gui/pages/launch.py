"""Launch Sessions: configure a run in the words of the job, not the CLI.

The Command page next door is a control per flag. This page is the same launcher
described the other way round - which environment, whose accounts, what to run,
how much to record - and :mod:`cms_gui.launch` does the translating. Nothing here
builds a command line; it builds a configuration and asks that module for the
argv, which is why a selection made here and the same selection made on the
Command page cannot mean two different things.

The page never shows a path the user has to type, a flag name, or a comma-
separated list to assemble by hand. The one exception is Advanced, folded away,
where a power user who does know what a flows directory is can still set one.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import launch, store, theme, widgets

SCENARIO_MODES = [
    (launch.SCENARIOS_NONE, "Just open the windows",
     "Sign every account in and leave the windows there."),
    (launch.SCENARIOS_ALL, "Run every scenario",
     "Everything the flows folder offers, template and manual ones excluded."),
    (launch.SCENARIOS_PER_USER, "Use each account's own list",
     "Each account runs the scenarios set for it on the Credentials page."),
    (launch.SCENARIOS_PICK, "Choose scenarios",
     "Pick them below. A tag pulls in every scenario carrying it."),
]

UNSAVED = "(unsaved)"
ALL_ENVIRONMENTS = "All environments"


class LaunchSessionsPage(QWidget):
    """The user-facing launcher: sections, a plain-words summary, and RUN."""

    run_requested = Signal()
    state_changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.inventory = None
        self.core = None
        self._building = False
        self._developer = bool(settings.developer_mode)
        self._configs = store.NamedConfigs(
            os.path.join(store.app_data_dir(), "configs.json"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(6)
        column.addWidget(widgets.heading("Launch Sessions"))
        column.addWidget(widgets.lede(
            "Choose what to open and what to run. Everything technical is worked "
            "out from these answers, so there is no command to write and no file "
            "to edit."))
        column.addSpacing(12)
        column.addWidget(self._config_bar())
        column.addSpacing(14)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        grid.addWidget(self._environment_panel(), 0, 0)
        grid.addWidget(self._users_panel(), 1, 0)
        grid.addWidget(self._extensions_panel(), 2, 0)
        grid.addWidget(self._sessions_panel(), 0, 1)
        grid.addWidget(self._scenarios_panel(), 1, 1)
        grid.addWidget(self._reports_panel(), 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        column.addLayout(grid)
        column.addSpacing(16)
        column.addWidget(self._advanced_panel())
        column.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        outer.addWidget(self._footer())
        self._restore()

    # -- construction ---------------------------------------------------------
    def _config_bar(self):
        panel = widgets.BlueprintPanel(padding=(14, 12, 14, 12))
        self.config_combo = QComboBox()
        self.config_combo.setMinimumWidth(240)
        self.config_combo.currentTextChanged.connect(self._config_selected)
        save = QPushButton("Save")
        save.clicked.connect(self.save_configuration)
        duplicate = QPushButton("Duplicate")
        duplicate.clicked.connect(self.duplicate_configuration)
        rename = QPushButton("Rename")
        rename.clicked.connect(self.rename_configuration)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_configuration)
        panel.layout().addWidget(widgets.row(
            widgets.kicker("saved configuration"), self.config_combo, None,
            save, duplicate, rename, delete))
        self._reload_configs()
        return panel

    def _environment_panel(self):
        panel = widgets.BlueprintPanel()
        panel.layout().addWidget(widgets.kicker("Environment"))
        self.env_combo = QComboBox()
        self.env_combo.addItem(ALL_ENVIRONMENTS)
        self.env_combo.currentTextChanged.connect(self._environment_changed)
        panel.layout().addWidget(widgets.field(
            "Which system to sign in to", self.env_combo,
            "Each environment supplies its own address, so no URL is needed."))
        self.env_note = widgets.mono("")
        panel.layout().addWidget(self.env_note)
        panel.layout().addStretch(1)
        return panel

    def _users_panel(self):
        panel = widgets.BlueprintPanel()
        self.users_mode = widgets.Segmented(["All accounts", "Choose accounts"])
        self.users_mode.changed.connect(lambda _v: self._users_mode_changed())
        panel.layout().addWidget(widgets.row(widgets.kicker("Users"), None,
                                             self.users_mode))
        self.users_list = widgets.CheckList(placeholder="Search accounts…")
        self.users_list.set_noun("accounts")
        self.users_list.changed.connect(self._changed)
        panel.layout().addWidget(self.users_list, 1)
        all_button = QPushButton("Select all")
        all_button.setProperty("variant", "ghost")
        all_button.clicked.connect(lambda: self.users_list.set_all(True))
        none_button = QPushButton("Select none")
        none_button.setProperty("variant", "ghost")
        none_button.clicked.connect(lambda: self.users_list.set_all(False))
        self.users_hint = QLabel("Accounts come from the Credentials page.")
        self.users_hint.setStyleSheet("font-size: 11px; color: %s;" % theme.NEUTRAL[600])
        panel.layout().addWidget(widgets.row(all_button, none_button, None,
                                             self.users_hint))
        return panel

    def _extensions_panel(self):
        panel = widgets.BlueprintPanel()
        self.ext_mode = widgets.Segmented(["All", "None", "Choose"])
        self.ext_mode.changed.connect(lambda _v: self._ext_mode_changed())
        panel.layout().addWidget(widgets.row(widgets.kicker("Extensions"), None,
                                             self.ext_mode))
        panel.layout().addWidget(widgets.lede(
            "Browser add-ons installed into every profile before it opens."))
        self.ext_list = widgets.CheckList(searchable=False)
        self.ext_list.set_noun("extensions")
        self.ext_list.list.setMinimumHeight(96)
        self.ext_list.changed.connect(self._changed)
        panel.layout().addWidget(self.ext_list, 1)
        return panel

    def _sessions_panel(self):
        panel = widgets.BlueprintPanel()
        panel.layout().addWidget(widgets.kicker("Sessions"))
        self.jobs = QSpinBox()
        self.jobs.setRange(1, 64)
        self.jobs.valueChanged.connect(self._changed)
        self.jobs_all = QCheckBox("All at once")
        self.jobs_all.toggled.connect(self._jobs_all_toggled)
        panel.layout().addWidget(widgets.field(
            "How many sessions run at the same time",
            widgets.row(self.jobs, self.jobs_all, None),
            "One at a time is the calmest to watch; more is faster."))
        self.keep_open = QCheckBox("Leave the windows open when the run finishes")
        self.keep_open.setChecked(True)
        self.keep_open.toggled.connect(self._changed)
        panel.layout().addWidget(self.keep_open)
        panel.layout().addStretch(1)
        return panel

    def _scenarios_panel(self):
        panel = widgets.BlueprintPanel()
        panel.layout().addWidget(widgets.kicker("Scenarios"))
        self.scenario_mode = QButtonGroup(self)
        self.scenario_mode.setExclusive(True)
        for index, (mode, label, hint) in enumerate(SCENARIO_MODES):
            button = QRadioButton(label)
            button.setToolTip(hint)
            self.scenario_mode.addButton(button, index)
            panel.layout().addWidget(button)
        self.scenario_mode.button(0).setChecked(True)
        self.scenario_mode.idToggled.connect(self._scenario_mode_changed)

        self.scenario_list = widgets.CheckList(placeholder="Search scenarios and tags…")
        self.scenario_list.set_noun("scenarios")
        self.scenario_list.changed.connect(self._changed)
        panel.layout().addWidget(self.scenario_list, 1)
        return panel

    def _reports_panel(self):
        panel = widgets.BlueprintPanel()
        panel.layout().addWidget(widgets.kicker("Reports and screenshots"))
        self.report_mode = widgets.Segmented(["Results only", "Full diagnostics",
                                             "Choose"])
        self.report_mode.changed.connect(lambda _v: self._report_mode_changed())
        panel.layout().addWidget(self.report_mode)
        self.report_list = widgets.CheckList(searchable=False)
        self.report_list.set_noun("artifacts")
        self.report_list.list.setMinimumHeight(90)
        self.report_list.changed.connect(self._changed)
        panel.layout().addWidget(self.report_list)

        self.shots = QComboBox()
        for mode in (launch.SHOTS_OFF, launch.SHOTS_FINISH,
                     launch.SHOTS_START_FINISH, launch.SHOTS_EACH):
            self.shots.addItem(launch.SHOT_LABELS[mode], mode)
        self.shots.currentIndexChanged.connect(self._changed)
        panel.layout().addWidget(widgets.field("Screenshots", self.shots))
        self.report_always = QCheckBox("Keep the full report even when everything "
                                       "passes")
        self.report_always.toggled.connect(self._changed)
        panel.layout().addWidget(self.report_always)
        panel.layout().addStretch(1)
        return panel

    def _advanced_panel(self):
        panel = widgets.BlueprintPanel()
        self.advanced = widgets.Disclosure("Advanced")
        panel.layout().addWidget(self.advanced)
        inner = self.advanced.body()
        inner.addWidget(widgets.lede(
            "Overrides most runs never need. Left empty, each of these follows "
            "the launcher's own default."))

        top = QHBoxLayout()
        top.setSpacing(16)
        self.url = QLineEdit()
        self.url.setPlaceholderText("supplied by the environment")
        self.url.textChanged.connect(self._changed)
        top.addWidget(widgets.field("Start address", self.url), 2)
        self.profile_prefix = QLineEdit()
        self.profile_prefix.setPlaceholderText("named after the environment")
        self.profile_prefix.textChanged.connect(self._changed)
        top.addWidget(widgets.field("Profile folder prefix", self.profile_prefix), 1)
        self.log_level = QComboBox()
        for level in launch.LOG_LEVELS:
            self.log_level.addItem(level)
        self.log_level.setCurrentText("INFO")
        self.log_level.currentTextChanged.connect(self._changed)
        top.addWidget(widgets.field("Log detail", self.log_level), 1)
        inner.addLayout(top)

        self.dir_edits = {}
        dirs = QHBoxLayout()
        dirs.setSpacing(16)
        for key, label in (("flows_dir", "Scenarios folder"),
                           ("reports_dir", "Reports folder"),
                           ("sessions_dir", "Profiles folder")):
            edit = QLineEdit()
            edit.setProperty("mono", True)
            edit.setPlaceholderText("(default)")
            edit.textChanged.connect(self._changed)
            browse = QPushButton(theme.glyph("browse"))
            browse.setFixedWidth(38)
            browse.clicked.connect(lambda _c=False, e=edit: self._browse(e))
            self.dir_edits[key] = edit
            dirs.addWidget(widgets.field(label, widgets.row(edit, browse)), 1)
        inner.addLayout(dirs)

        self.overlay = QCheckBox("Show an execution overlay inside each window")
        self.overlay.toggled.connect(self._overlay_toggled)
        inner.addWidget(self.overlay)
        self.overlay_list = widgets.CheckList(searchable=False)
        self.overlay_list.set_noun("overlay parts")
        self.overlay_list.list.setMaximumHeight(120)
        self.overlay_list.changed.connect(self._changed)
        inner.addWidget(self.overlay_list)
        self.detach = QCheckBox("Leave the windows running after the launcher exits")
        self.detach.toggled.connect(self._changed)
        inner.addWidget(self.detach)
        return panel

    def _footer(self):
        footer = QWidget()
        widgets.scoped_style(footer, "background: %s; border-top: 1px solid %s;"
                             % (theme.NEUTRAL[100], theme.DIVIDER))
        column = QVBoxLayout(footer)
        column.setContentsMargins(24, 10, 24, 10)
        column.setSpacing(6)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary.setStyleSheet("font-size: 12px; color: %s;" % theme.NEUTRAL[800])
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset)
        save = QPushButton("Save")
        save.clicked.connect(self.save_configuration)
        self.run_button = QPushButton(theme.labelled("run", "RUN"))
        self.run_button.setProperty("variant", "primary")
        self.run_button.clicked.connect(self.run_requested.emit)
        # Built by hand rather than with widgets.row: the summary is the long part
        # and has to take the leftover width, which needs a stretch factor on that
        # one widget instead of a spacer beside it.
        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(14)
        strip.addWidget(widgets.kicker("summary"))
        strip.addWidget(self.summary, 1)
        for button in (reset, save, self.run_button):
            strip.addWidget(button)
        column.addLayout(strip)

        self.problems = QLabel("")
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("font-size: 12px; color: %s;" % theme.BAD)
        column.addWidget(self.problems)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("font-size: 11px; color: %s;" % theme.NEUTRAL[600])
        column.addWidget(self.notes)
        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview.setStyleSheet("font-family: %s; font-size: 11px; color: %s;"
                                   % (theme.MONO_CSS, theme.ACCENT_RAMP[800]))
        column.addWidget(self.preview)
        return footer

    # -- state ----------------------------------------------------------------
    def state(self):
        """The configuration as the user has it, in Launch Sessions' own words."""
        alias = self.env_combo.currentText()
        return {
            "environment": "" if alias == ALL_ENVIRONMENTS else alias,
            "users": {"mode": (launch.USERS_PICK
                               if self.users_mode.current() == "Choose accounts"
                               else launch.USERS_ALL),
                      "logins": self.users_list.checked()},
            "sessions": {"jobs": self.jobs.value(),
                         "all_at_once": self.jobs_all.isChecked(),
                         "keep_open": self.keep_open.isChecked(),
                         "detach": self.detach.isChecked()},
            "extensions": {"mode": self._ext_mode_value(),
                           "names": self.ext_list.checked()},
            "scenarios": {"mode": SCENARIO_MODES[self._scenario_mode_id()][0],
                          "selected": self.scenario_list.checked()},
            "reports": {"level": self._report_mode_value(),
                        "artifacts": self.report_list.checked(),
                        "always": self.report_always.isChecked()},
            "screenshots": {"mode": self.shots.currentData() or launch.SHOTS_OFF},
            "overlay": {"enabled": self.overlay.isChecked(),
                        "components": self.overlay_list.checked()},
            "advanced": {"url": self.url.text().strip(),
                         "profile_prefix": self.profile_prefix.text().strip(),
                         "log_level": self.log_level.currentText(),
                         "flows_dir": self.dir_edits["flows_dir"].text().strip(),
                         "reports_dir": self.dir_edits["reports_dir"].text().strip(),
                         "sessions_dir": self.dir_edits["sessions_dir"].text().strip()},
        }

    def set_state(self, config):
        """Load a configuration into the controls (from a save or from history)."""
        config = launch.merged(config)
        self._building = True
        try:
            alias = config["environment"]
            self.env_combo.setCurrentText(alias or ALL_ENVIRONMENTS)

            users = config["users"]
            self.users_mode.set_current(
                "Choose accounts" if users["mode"] == launch.USERS_PICK
                else "All accounts", notify=False)
            self._populate_users(users["logins"])

            sessions = config["sessions"]
            self.jobs.setValue(max(1, int(sessions["jobs"] or 1)))
            self.jobs_all.setChecked(bool(sessions["all_at_once"]))
            self.keep_open.setChecked(bool(sessions["keep_open"]))
            self.detach.setChecked(bool(sessions["detach"]))

            extensions = config["extensions"]
            self.ext_mode.set_current({launch.EXT_NONE: "None",
                                       launch.EXT_PICK: "Choose"}.get(
                                          extensions["mode"], "All"), notify=False)
            self._populate_extensions(extensions["names"])

            scenarios = config["scenarios"]
            for index, (mode, _label, _hint) in enumerate(SCENARIO_MODES):
                if mode == scenarios["mode"]:
                    self.scenario_mode.button(index).setChecked(True)
                    break
            self._populate_scenarios(scenarios["selected"])

            reports = config["reports"]
            self.report_mode.set_current({launch.REPORTS_FULL: "Full diagnostics",
                                          launch.REPORTS_CUSTOM: "Choose"}.get(
                                             reports["level"], "Results only"),
                                         notify=False)
            self._populate_artifacts(reports["artifacts"])
            self.report_always.setChecked(bool(reports["always"]))
            index = self.shots.findData(config["screenshots"]["mode"])
            self.shots.setCurrentIndex(index if index >= 0 else 0)

            self.overlay.setChecked(bool(config["overlay"]["enabled"]))
            self._populate_overlay(config["overlay"]["components"])

            advanced = config["advanced"]
            self.url.setText(advanced["url"])
            self.profile_prefix.setText(advanced["profile_prefix"])
            self.log_level.setCurrentText(advanced["log_level"] or "INFO")
            for key, edit in self.dir_edits.items():
                edit.setText(advanced[key])
        finally:
            self._building = False
        self._changed()

    def argv(self):
        return launch.argv(self.state(), self.inventory)

    def problem_list(self):
        return launch.validate(self.state(), self.inventory)

    def run_meta(self):
        """One line for the Run page's header."""
        config = self.state()
        return " · ".join([launch.users_label(config, self.inventory),
                           launch.scenarios_label(config),
                           launch.sessions_label(config)])

    def describe_line(self):
        return launch.describe_line(self.state(), self.inventory)

    def summary_rows(self):
        return launch.summarise(self.state(), self.inventory)

    # -- reactions ------------------------------------------------------------
    def _changed(self, *_args):
        if self._building:
            return
        config = self.state()
        self.users_list.setEnabled(config["users"]["mode"] == launch.USERS_PICK)
        self.ext_list.setEnabled(config["extensions"]["mode"] == launch.EXT_PICK)
        self.scenario_list.setEnabled(
            config["scenarios"]["mode"] == launch.SCENARIOS_PICK)
        self.report_list.setEnabled(
            config["reports"]["level"] == launch.REPORTS_CUSTOM)
        self.overlay_list.setEnabled(config["overlay"]["enabled"])
        self.jobs.setEnabled(not config["sessions"]["all_at_once"])

        rows = launch.summarise(config, self.inventory)
        self.summary.setText("   ·   ".join("%s: %s" % (label, value)
                                            for label, value in rows))
        problems = launch.validate(config, self.inventory)
        self.problems.setText("   ".join(problems))
        self.problems.setVisible(bool(problems))
        notes = launch.notes(config, self.inventory)
        self.notes.setText("   ".join(notes))
        self.notes.setVisible(bool(notes))
        self.run_button.setEnabled(not problems and not self._running())
        self.preview.setText(launch.preview(config, self.inventory, self.core))
        self.preview.setVisible(self._developer)

        self.settings.save_launch_state(config)
        self.state_changed.emit()

    def _running(self):
        return getattr(self, "_is_running", False)

    def _environment_changed(self, _text):
        if self._building:
            return
        # Keep whatever is still available; an account only in the old
        # environment cannot be part of this run any more.
        self._populate_users(self.users_list.checked())
        self._changed()

    def _users_mode_changed(self):
        if not self._building and self.users_mode.current() == "Choose accounts" \
                and not self.users_list.checked():
            self.users_list.set_all(True)     # "choose" starts from everything
        self._changed()

    def _ext_mode_changed(self):
        if not self._building and self.ext_mode.current() == "Choose" \
                and not self.ext_list.checked():
            self.ext_list.set_all(True)
        self._changed()

    def _report_mode_changed(self):
        if not self._building and self.report_mode.current() == "Choose" \
                and not self.report_list.checked():
            self.report_list.set_checked(["result"])
        self._changed()

    def _scenario_mode_changed(self, _id, checked):
        if checked:
            self._changed()

    def _jobs_all_toggled(self, _checked):
        self._changed()

    def _overlay_toggled(self, checked):
        if not self._building and checked and not self.overlay_list.checked():
            self.overlay_list.set_all(True)
        self._changed()

    # -- reading the controls -------------------------------------------------
    def _ext_mode_value(self):
        return {"None": launch.EXT_NONE, "Choose": launch.EXT_PICK}.get(
            self.ext_mode.current(), launch.EXT_ALL)

    def _report_mode_value(self):
        return {"Full diagnostics": launch.REPORTS_FULL,
                "Choose": launch.REPORTS_CUSTOM}.get(self.report_mode.current(),
                                                     launch.REPORTS_RESULTS)

    def _scenario_mode_id(self):
        checked = self.scenario_mode.checkedId()
        return checked if checked >= 0 else 0

    # -- context --------------------------------------------------------------
    def set_core(self, core):
        self.core = core
        self._changed()

    def set_inventory(self, inventory):
        """Repopulate every list from what the core says exists."""
        self.inventory = inventory
        config = self.state()
        self._building = True
        try:
            alias = config["environment"]
            self.env_combo.blockSignals(True)
            self.env_combo.clear()
            self.env_combo.addItem(ALL_ENVIRONMENTS)
            for env in inventory.envs:
                self.env_combo.addItem(env.get("alias", ""))
            self.env_combo.setCurrentText(alias or ALL_ENVIRONMENTS)
            self.env_combo.blockSignals(False)

            self._populate_users(config["users"]["logins"])
            self._populate_extensions(config["extensions"]["names"])
            self._populate_scenarios(config["scenarios"]["selected"])
            self._populate_artifacts(config["reports"]["artifacts"])
            self._populate_overlay(config["overlay"]["components"])

            for key, name in (("flows_dir", "flows"), ("reports_dir", "reports"),
                              ("sessions_dir", "sessions")):
                edit = self.dir_edits[key]
                # The Environments page owns the defaults; adopt them silently
                # while the user has not typed one of their own.
                stored = self.settings.directory(name)
                if stored and not edit.text():
                    edit.setText(stored)
                if not edit.text():
                    edit.setPlaceholderText(inventory.dirs().get(name) or "(default)")
        finally:
            self._building = False
        self._update_env_note()
        self._changed()

    def set_developer_mode(self, enabled):
        self._developer = bool(enabled)
        self.preview.setVisible(self._developer)

    def set_running(self, running):
        self._is_running = bool(running)
        self.run_button.setEnabled(not running and not self.problem_list())

    # -- populating -----------------------------------------------------------
    def _populate_users(self, keep):
        """Accounts for the chosen environment, with ``keep`` still ticked.

        A login in ``keep`` that the inventory no longer offers is added anyway,
        marked as missing: dropping it silently would quietly change a saved
        configuration the moment it was opened.
        """
        alias = self.env_combo.currentText()
        alias = "" if alias == ALL_ENVIRONMENTS else alias
        rows = []
        if self.inventory:
            env_value = self.inventory.env_value(alias) if alias else None
            for user in self.inventory.users:
                if env_value is not None and user.get("env") != env_value:
                    continue
                note = user.get("class", "")
                if not alias:
                    note = "%s   %s" % (user.get("env", ""), note)
                rows.append((user.get("login", ""), note))
        known = {login for login, _note in rows}
        for login in keep or ():
            if login not in known:
                rows.append((login, "(not in this environment)"))
        self.users_list.clear()
        for login, note in rows:
            self.users_list.add(login, login, note)
        self.users_list.set_checked(keep)
        self._update_env_note()

    def _populate_extensions(self, keep):
        self.ext_list.clear()
        seen = set()
        for extension in (self.inventory.extensions if self.inventory else []):
            name = extension.get("name", "")
            if not name or name in seen:
                continue       # the same name can arrive twice (local and store)
            seen.add(name)
            usable = bool(extension.get("usable", True))
            note = extension.get("kind", "")
            if not usable:
                note = "unavailable: %s" % (extension.get("reason") or "unknown")
            self.ext_list.add(name, name, note, enabled=usable)
        for name in keep or ():
            if name not in seen:
                self.ext_list.add(name, name, "(not installed)")
                seen.add(name)
        self.ext_list.set_checked(keep)

    def _populate_scenarios(self, keep):
        self.scenario_list.clear()
        known = set()
        for tag in (self.inventory.tags if self.inventory else []):
            value = "tag:%s" % tag
            known.add(value)
            self.scenario_list.add(value, value, "every scenario with this tag",
                                   accent=True)
        for scenario in (self.inventory.scenarios if self.inventory else []):
            value = scenario.get("id", "")
            if not value:
                continue
            known.add(value)
            note = scenario.get("name") or ""
            if not scenario.get("in_all", True):
                note += "   (not part of \"every scenario\")"
            self.scenario_list.add(value, value, note)
        for value in keep or ():
            if value not in known:
                self.scenario_list.add(value, value, "(not found)")
                known.add(value)
        self.scenario_list.set_checked(keep)

    def _populate_artifacts(self, keep):
        self.report_list.clear()
        choices = (self.inventory.choices("report_artifacts", launch.ALL_ARTIFACTS)
                   if self.inventory else launch.ALL_ARTIFACTS)
        notes = {"console": "browser console output", "dom": "the page's HTML",
                 "result": "pass/fail detail", "screen": "screenshots",
                 "url": "the address each step ended on"}
        for choice in choices:
            self.report_list.add(choice, choice, notes.get(choice, ""))
        self.report_list.set_checked(keep)

    def _populate_overlay(self, keep):
        self.overlay_list.clear()
        choices = (self.inventory.choices("overlay_components", launch.ALL_OVERLAY)
                   if self.inventory else launch.ALL_OVERLAY)
        for choice in choices:
            self.overlay_list.add(choice, choice)
        self.overlay_list.set_checked(keep)

    def _update_env_note(self):
        if not self.inventory:
            self.env_note.setText("")
            return
        alias = self.env_combo.currentText()
        if alias == ALL_ENVIRONMENTS:
            self.env_note.setText("%d environments · %d accounts"
                                  % (len(self.inventory.envs),
                                     len(self.inventory.users)))
            return
        for env in self.inventory.envs:
            if env.get("alias") == alias:
                self.env_note.setText("%s · %s accounts"
                                      % (env.get("origin") or "no address",
                                         env.get("count", "?")))
                return
        self.env_note.setText("")

    # -- saved configurations -------------------------------------------------
    def _reload_configs(self, select=None):
        self.config_combo.blockSignals(True)
        self.config_combo.clear()
        self.config_combo.addItem(UNSAVED)
        for name in self._configs.names():
            self.config_combo.addItem(name)
        target = select if select is not None else self.settings.launch_config_name
        index = self.config_combo.findText(target or UNSAVED)
        self.config_combo.setCurrentIndex(index if index >= 0 else 0)
        self.config_combo.blockSignals(False)

    def _current_config_name(self):
        name = self.config_combo.currentText()
        return "" if name == UNSAVED else name

    def _config_selected(self, name):
        if self._building:
            return
        self.settings.launch_config_name = "" if name == UNSAVED else name
        if name and name != UNSAVED:
            saved = self._configs.get(name)
            if saved is not None:
                self.set_state(saved)

    def save_configuration(self):
        name = self._current_config_name()
        if not name:
            name, ok = QInputDialog.getText(self, "Save configuration",
                                            "Name this configuration:")
            name = (name or "").strip()
            if not ok or not name:
                return
            name = self._configs.unique_name(name)
        self._configs.put(name, self.state())
        self.settings.launch_config_name = name
        self._reload_configs(select=name)

    def duplicate_configuration(self):
        base = self._current_config_name() or "Launch configuration"
        name = self._configs.unique_name(base)
        self._configs.put(name, self.state())
        self.settings.launch_config_name = name
        self._reload_configs(select=name)

    def rename_configuration(self):
        old = self._current_config_name()
        if not old:
            QMessageBox.information(self, "Rename",
                                    "Save this configuration first.")
            return
        new, ok = QInputDialog.getText(self, "Rename configuration",
                                       "New name:", text=old)
        new = (new or "").strip()
        if not ok or not new or new == old:
            return
        self._configs.rename(old, self._configs.unique_name(new))
        self.settings.launch_config_name = new
        self._reload_configs(select=new)

    def delete_configuration(self):
        name = self._current_config_name()
        if not name:
            return
        answer = QMessageBox.question(
            self, "Delete configuration",
            "Delete \"%s\"? The current settings stay as they are." % name,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self._configs.remove(name)
        self.settings.launch_config_name = ""
        self._reload_configs(select=UNSAVED)

    # -- helpers --------------------------------------------------------------
    def reset(self):
        self.set_state(launch.DEFAULTS)
        self.config_combo.setCurrentIndex(0)

    def _browse(self, edit):
        start = edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        if chosen:
            edit.setText(chosen)

    def _restore(self):
        saved = self.settings.launch_state()
        self.set_state(saved if saved else launch.DEFAULTS)
