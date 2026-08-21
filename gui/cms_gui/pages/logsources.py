"""Log sources page: connections and logs for ``--server-log``.

Two tables, because the file has two levels and conflating them is what makes a
flat list of "sources" unusable. A **connection** says *where* to run a reader -
this machine, or one ssh hop away - and a **log** says *what* to read there. One
connection serves every log on a machine, so a stand with three logs opens one
ssh connection rather than three.

**Rows are created in a dialog, not in the grid.** An inline row means typing
eight narrow columns in a fixed order with nothing saying what each one wants, and
the fields that matter depend on choices made in the same row - an ssh connection
needs a host and a local one must not have one; a log's target is a path, a
container, a unit or a URL depending on its type. A form can show exactly the
fields that apply and explain them; a table cannot. So the tables are a readable
overview and every edit opens :class:`ConnectionDialog` or :class:`LogDialog`.

**Nothing here is coupled to one backend.** Format presets are named after the
*shape* of a line (`iso`, `slash`, `clf`, `syslog`, `none`), with application names
offered as aliases, and "custom" takes your own timestamp/level patterns for a
backend no preset describes.

The file is the launcher's, so this page keeps it exactly as valid as the CLI
expects: every rule in ``logsourcesfile.validate`` mirrors one in
``engine.serverlog``, and Save is disabled until they all hold. Test asks the
*core* whether a log can really be read (``--server-log-test``) - the GUI never
imports the engine, so it has no ssh, no docker and no tail of its own.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .. import logsourcesfile as lsf
from .. import theme, widgets

#: Label for the one field whose meaning changes with the log's type, and what to
#: say underneath it. The whole reason a log is edited in a form.
TARGET_LABELS = {
    "file": ("Path", "Absolute path to the log file on that machine."),
    "docker": ("Container", "Container name or id; read with `docker logs -f`."),
    "journal": ("Unit", "systemd unit, or leave empty for the whole journal."),
    "http": ("URL", "A line-oriented HTTP stream (plain text or server-sent events)."),
}

TYPE_HINTS = {
    "local": "Readers run on this machine.",
    "ssh": "Readers run over ssh. The host key is not accepted automatically - "
           "ssh in by hand once first.",
}


def _form(parent):
    column = QVBoxLayout(parent)
    column.setContentsMargins(20, 18, 20, 16)
    column.setSpacing(10)
    return column


class _RowDialog(QDialog):
    """Shared shell: a scrolling form, a live problem line, and OK gated on validity.

    The form scrolls because the log form grows: revealing the custom-format
    patterns takes it past a thousand pixels, which is taller than a laptop
    screen, and a dialog whose OK button is off the bottom edge cannot be
    completed at all. The problem line and the buttons stay outside the scroll,
    so what is wrong and how to leave are always in view.
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(580)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._body = QWidget()
        self.column = _form(self._body)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll, 1)

        self.problem = QLabel("")
        self.problem.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer = QWidget()
        footer_column = QVBoxLayout(footer)
        footer_column.setContentsMargins(20, 8, 20, 14)
        footer_column.setSpacing(8)
        footer_column.addWidget(self.problem)
        footer_column.addWidget(self.buttons)
        outer.addWidget(footer)

    def finish_layout(self):
        self.column.addStretch(1)
        self._fit()

    def _fit(self):
        """Open at the form's natural height, but never taller than the screen."""
        wanted = self._body.sizeHint().height() + 110
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 900
        self.resize(self.width() or 580, min(wanted, int(available * 0.9)))

    def show_problems(self, problems):
        """Report what is still wrong and gate OK on it. Returns True when valid."""
        # Only the row being edited: validate() reports on the whole file, and a
        # problem in another row is not something this dialog can fix.
        self.problem.setText("\n".join(problems[:3]))
        self.problem.setStyleSheet("color: %s; font-size: 12px;"
                                   % (theme.BAD if problems else theme.NEUTRAL[700]))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not problems)
        return not problems


class ConnectionDialog(_RowDialog):
    """Create or edit one connection: where readers run."""

    def __init__(self, row=None, taken=(), parent=None):
        super().__init__("Connection" if row else "New connection", parent)
        self._row = (row or lsf.ConnectionRow(type="local")).copy()
        self._taken = {name for name in taken if name != self._row.name}

        self.name = QLineEdit(self._row.name)
        self.name.setPlaceholderText("a short name, e.g. staging")
        self.name.textChanged.connect(self._changed)
        self.column.addWidget(widgets.field(
            "Name", self.name, "Logs refer to this connection by name."))

        self.type = QComboBox()
        self.type.addItems(list(lsf.CONNECTION_TYPES))
        self.type.setCurrentText(self._row.type)
        self.type.currentTextChanged.connect(self._type_changed)
        self.type_hint = QLabel("")
        self.type_hint.setProperty("role", "hint")
        self.type_hint.setWordWrap(True)
        self.column.addWidget(widgets.field("Runs on", self.type))
        self.column.addWidget(self.type_hint)

        self.host = QLineEdit(self._row.host)
        self.host.setPlaceholderText("staging.example.com")
        self.host.textChanged.connect(self._changed)
        self.host_field = widgets.field("Host", self.host)
        self.column.addWidget(self.host_field)

        self.user = QLineEdit(self._row.user)
        self.user.setPlaceholderText("deploy   (optional - ~/.ssh/config may say)")
        self.user.textChanged.connect(self._changed)
        self.user_field = widgets.field("User", self.user)
        self.column.addWidget(self.user_field)

        self.identity = QLineEdit(self._row.identity)
        self.identity.setPlaceholderText("~/.ssh/id_ed25519   (optional)")
        self.identity.textChanged.connect(self._changed)
        browse = QPushButton("Browse…")
        browse.setProperty("variant", "ghost")
        browse.clicked.connect(self._pick_identity)
        self.identity_field = widgets.field(
            "Identity file", widgets.row(self.identity, browse))
        self.column.addWidget(self.identity_field)

        self.port = QLineEdit(str(self._row.port or ""))
        self.port.setPlaceholderText("22")
        self.port.textChanged.connect(self._changed)
        self.port_field = widgets.field("Port", self.port)
        self.column.addWidget(self.port_field)

        self.options = QLineEdit(", ".join(self._row.options))
        self.options.setPlaceholderText("ConnectTimeout=5, ServerAliveInterval=30")
        self.options.textChanged.connect(self._changed)
        self.options_field = widgets.field(
            "Extra ssh options", self.options,
            "Comma-separated, each passed as -o.")
        self.column.addWidget(self.options_field)

        self.finish_layout()
        self._type_changed(self.type.currentText())

    def _pick_identity(self):
        start = os.path.expanduser("~/.ssh")
        path, _ = QFileDialog.getOpenFileName(self, "Private key",
                                              start if os.path.isdir(start) else "")
        if path:
            self.identity.setText(path)

    def _type_changed(self, value):
        remote = value == "ssh"
        for field in (self.host_field, self.user_field, self.identity_field,
                      self.port_field, self.options_field):
            field.setVisible(remote)
        self.type_hint.setText(TYPE_HINTS.get(value, ""))
        self._fit()
        self._changed()

    def _changed(self, *_args):
        self.show_problems(self._problems())

    def _problems(self):
        row = self.value()
        # The file's own rules, on this row alone, plus the one thing validate()
        # can only see across rows.
        problems = lsf.validate([row], [])
        if row.name and row.name in self._taken:
            problems.append("connection 1 (%s): that name is already used." % row.name)
        return problems

    def value(self):
        return lsf.ConnectionRow(
            name=self.name.text().strip(), type=self.type.currentText(),
            host=self.host.text().strip(), user=self.user.text().strip(),
            identity=self.identity.text().strip(), port=self.port.text().strip(),
            options=[o.strip() for o in self.options.text().split(",") if o.strip()],
            extra=dict(self._row.extra))


class LogDialog(_RowDialog):
    """Create or edit one log: what to read, where it belongs, how to parse it."""

    def __init__(self, row=None, connections=(), envs=(), siblings=(),
                 developer=False, parent=None):
        super().__init__("Log" if row else "New log", parent)
        self._row = (row or lsf.LogRow()).copy()
        self._connections = [c.name for c in connections if c.name]
        self._siblings = list(siblings)
        # Fixed for the life of the dialog: it is opened, answered and closed,
        # so there is no mode switch to follow the way a page has to.
        self._developer = bool(developer)

        self.name = QLineEdit(self._row.name)
        self.name.setPlaceholderText("app, nginx, worker…")
        self.name.textChanged.connect(self._changed)
        self.column.addWidget(widgets.field(
            "Name", self.name,
            "How %s refers to it. Unique per environment, so the same name may "
            "repeat on another stand."
            % ("--server-log" if self._developer else "a run")))

        self.connection = QComboBox()
        self.connection.addItems(self._connections or [self._row.connection])
        if self._row.connection:
            self.connection.setCurrentText(self._row.connection)
        self.connection.currentTextChanged.connect(self._changed)
        self.column.addWidget(widgets.field(
            "Read it over", self.connection,
            "Add connections above if the one you need is not here."))

        self.envs = widgets.CheckList(searchable=False)
        self.envs.set_noun("environments")
        # Bounded: a QListWidget expands to whatever the layout will give it, and
        # in a scrolling form that is all of it - pushing every field below the
        # fold behind an empty white box.
        self.envs.list.setMinimumHeight(76)
        self.envs.list.setMaximumHeight(120)
        known = list(envs)
        for env in self._row.envs:
            if env not in known:
                known.append(env)
        for env in known:
            self.envs.add(env, env, "")
        self.envs.set_checked(self._row.envs)
        self.envs.changed.connect(self._changed)
        self.column.addWidget(widgets.field(
            "Environments", self.envs,
            "Spelled exactly as in users.json - that string is what ties this log "
            "to the windows opened against it."))

        self.other_envs = QLineEdit("")
        self.other_envs.setPlaceholderText("another environment, comma-separated")
        self.other_envs.textChanged.connect(self._changed)
        self.column.addWidget(widgets.field("Other environments", self.other_envs))

        self.type = QComboBox()
        self.type.addItems(list(lsf.LOG_TYPES))
        self.type.setCurrentText(self._row.type)
        self.type.currentTextChanged.connect(self._type_changed)
        self.column.addWidget(widgets.field("Kind", self.type))

        self.target = QLineEdit(self._row.target)
        self.target.textChanged.connect(self._changed)
        self.target_browse = QPushButton("Browse…")
        self.target_browse.setProperty("variant", "ghost")
        self.target_browse.clicked.connect(self._pick_target)
        self.target_field = widgets.field("Path",
                                          widgets.row(self.target,
                                                      self.target_browse))
        self.column.addWidget(self.target_field)
        self.target_hint = QLabel("")
        self.target_hint.setProperty("role", "hint")
        self.target_hint.setWordWrap(True)
        self.column.addWidget(self.target_hint)

        self.format = QComboBox()
        for shape in lsf.FORMAT_SHAPES:
            self.format.addItem(shape)
        self.format.insertSeparator(self.format.count())
        for alias in sorted(lsf.FORMAT_ALIASES):
            self.format.addItem(alias)
        self.format.insertSeparator(self.format.count())
        self.format.addItem(lsf.CUSTOM_FORMAT)
        self.format.setCurrentText(self._row.format_label())
        self.format.currentTextChanged.connect(self._format_changed)
        self.column.addWidget(widgets.field(
            "Line format", self.format,
            "Presets are named after the shape of a line; the rest are aliases for "
            "one of them. Pick \"custom\" for anything they do not describe."))
        self.format_hint = QLabel("")
        self.format_hint.setProperty("role", "hint")
        self.format_hint.setWordWrap(True)
        self.column.addWidget(self.format_hint)

        self.tz = QComboBox()
        self.tz.setEditable(True)
        self.tz.addItems(["local", "utc"])
        self.tz.setCurrentText(self._row.tz or "local")
        self.tz.currentTextChanged.connect(self._changed)
        self.column.addWidget(widgets.field(
            "Written in", self.tz,
            "The clock the timestamps use, when the line carries no offset of its "
            "own. A backend logging UTC on a machine that is not is why a log can "
            "stream nothing at all."))

        stamp = self._row.timestamp or {}
        self.ts_regex = QLineEdit(stamp.get("regex", ""))
        self.ts_regex.setPlaceholderText(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
        self.ts_regex.textChanged.connect(self._changed)
        self.ts_regex_field = widgets.field(
            "Timestamp pattern", self.ts_regex,
            "One capturing group around the timestamp.")
        self.column.addWidget(self.ts_regex_field)

        self.ts_format = QLineEdit(stamp.get("format", "") or "iso")
        self.ts_format.setPlaceholderText("%Y-%m-%d %H:%M:%S   or   iso")
        self.ts_format.textChanged.connect(self._changed)
        self.ts_format_field = widgets.field(
            "Timestamp reading", self.ts_format,
            "A strptime pattern, or \"iso\" / \"clf\" for those shapes.")
        self.column.addWidget(self.ts_format_field)

        self.level_regex = QLineEdit((self._row.level or {}).get("regex", ""))
        self.level_regex.setPlaceholderText(r"\b(DEBUG|INFO|WARN|ERROR)\b")
        self.level_regex.textChanged.connect(self._changed)
        self.level_field = widgets.field(
            "Level pattern", self.level_regex,
            "Optional; one capturing group. Only tints the line.")
        self.column.addWidget(self.level_field)

        self.default = QCheckBox("Stream this one by default")
        self.default.setChecked(self._row.default)
        self.default.setToolTip(
            "%s takes the logs marked default for the environment being launched."
            % ("A bare --server-log" if self._developer
               else "A run that names no log"))
        self.default.toggled.connect(self._changed)
        self.column.addWidget(self.default)

        self.finish_layout()
        self._type_changed(self.type.currentText())
        self._format_changed(self.format.currentText())

    # -- reactions ---------------------------------------------------------
    def _pick_target(self):
        path, _ = QFileDialog.getOpenFileName(self, "Log file", "/var/log")
        if path:
            self.target.setText(path)

    def _type_changed(self, value):
        label, hint = TARGET_LABELS.get(value, TARGET_LABELS["file"])
        self.target_field.label.setText(label)
        self.target_hint.setText(hint)
        # Only a local file can be browsed for; a container or a remote path is
        # not on this machine to point at.
        self.target_browse.setVisible(value == "file")
        self._changed()

    def _format_changed(self, value):
        custom = value == lsf.CUSTOM_FORMAT
        for field in (self.ts_regex_field, self.ts_format_field, self.level_field):
            field.setVisible(custom)
        self.format_hint.setText(
            "Your own patterns, for a backend no preset describes." if custom
            else lsf.FORMAT_EXAMPLES.get(lsf.FORMAT_ALIASES.get(value, value), ""))
        self._fit()
        self._changed()

    def _changed(self, *_args):
        self.show_problems(self._problems())

    def _problems(self):
        row = self.value()
        problems = [p for p in lsf.validate([], [row])
                    if "connection" not in p or "no connection named" not in p]
        if not self._connections:
            problems.append("log 1: add a connection first - a log has to be read "
                            "over one.")
        for other in self._siblings:
            shared = set(other.envs) & set(row.envs)
            if row.name and other.name == row.name and shared:
                problems.append("log 1 (%s): that name is already used for %s."
                                % (row.name, ", ".join(sorted(shared))))
                break
        return problems

    def value(self):
        envs = list(self.envs.checked())
        for extra in self.other_envs.text().split(","):
            if extra.strip() and extra.strip() not in envs:
                envs.append(extra.strip())
        custom = self.format.currentText() == lsf.CUSTOM_FORMAT
        timestamp = level = None
        fmt = self._row.format if custom else self.format.currentText()
        if custom:
            timestamp = {"regex": self.ts_regex.text().strip(),
                         "format": self.ts_format.text().strip() or "iso"}
            if self.level_regex.text().strip():
                level = {"regex": self.level_regex.text().strip()}
        return lsf.LogRow(
            name=self.name.text().strip(), connection=self.connection.currentText(),
            envs=envs, type=self.type.currentText(),
            target=self.target.text().strip(), format=fmt,
            default=self.default.isChecked(), headers=dict(self._row.headers),
            timestamp=timestamp, level=level,
            tz=self.tz.currentText().strip() or "local",
            extra=dict(self._row.extra))


class LogViewerDialog(QDialog):
    """What one log holds right now, read through the core.

    Read-only and one-shot on purpose: this is for looking at a log while setting
    it up - is this the right file, is anything in it, does it look the way the
    format expects. Following it live is what a run does, into the session panels.
    """

    def __init__(self, name, result, whole, parent=None):
        super().__init__(parent)
        self._lines = list(result.get("lines") or [])
        self.setWindowTitle("%s - %s" % (name, "whole log" if whole else "tail"))
        self.resize(1000, 620)

        column = QVBoxLayout(self)
        column.setContentsMargins(20, 16, 20, 14)
        column.setSpacing(8)
        column.addWidget(widgets.mono(result.get("target", "")))

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter these lines…")
        self.filter.textChanged.connect(self._repaint)
        self.count = widgets.mono("")
        save = QPushButton("Save…")
        save.setProperty("variant", "ghost")
        save.clicked.connect(self._save)
        column.addWidget(widgets.row(self.filter, self.count, None, save))

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setStyleSheet("font-family: %s; font-size: 12px;" % theme.MONO_CSS)
        column.addWidget(self.view, 1)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        column.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

        if result.get("truncated"):
            self._say("Showing the end of the log - it is longer than this.")
        elif result.get("empty"):
            self._say("The log is readable and empty. That is not a fault.")
        self._repaint()

    def _say(self, text):
        self.note.setText(text)
        self.note.setStyleSheet("color: %s; font-size: 12px;" % theme.NEUTRAL[700])

    def _visible(self):
        needle = self.filter.text().strip().lower()
        if not needle:
            return self._lines
        return [line for line in self._lines if needle in line.lower()]

    def _repaint(self):
        lines = self._visible()
        self.view.setPlainText("\n".join(lines))
        self.count.setText("%d of %d line%s" % (len(lines), len(self._lines),
                                                "" if len(self._lines) == 1 else "s"))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())          # a log is read from its end

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "%s.log"
                                              % self.windowTitle().split(" - ")[0],
                                              "Log files (*.log);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self._visible()))
        except OSError as exc:
            QMessageBox.warning(self, "Save log", str(exc))


class LogSourcesPage(QWidget):
    """Edit, validate and save the backend logs --server-log can stream."""

    saved = Signal()

    CONN_HEADERS = ["Name", "Runs on", "Used by", ""]
    C_NAME, C_WHERE, C_USED, C_ACTIONS = range(4)

    LOG_HEADERS = ["Name", "Environments", "Reads", "Over", "Format", "Default", ""]
    (L_NAME, L_ENVS, L_READS, L_CONN, L_FORMAT, L_DEFAULT, L_ACTIONS) = range(7)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = ""
        self._connections = []
        self._logs = []
        self._fingerprint = None
        # Set when the file on disk could not be read at all. Kept until a load
        # succeeds, because the alternative is an empty editor over a file full
        # of content: validation would then call the empty document fine and Save
        # would quietly replace whatever could not be parsed.
        self._load_error = ""
        self._envs = []          # from --describe, so environments are picked not typed
        self._core = None
        self._viewers = []       # open log windows, kept from being collected
        self._developer = False  # which register this page and its dialogs use

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(6)

        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(lambda: self.load(self._path))
        self.save_button = QPushButton("Save")
        self.save_button.setProperty("variant", "primary")
        self.save_button.clicked.connect(self.save)
        column.addWidget(widgets.row(widgets.heading("Log sources"), None,
                                     self.reload_button, self.save_button))

        self.path_label = widgets.mono("")
        column.addWidget(self.path_label)
        self.wording = widgets.Phrasing()
        _tail = ("connection is where to run a reader; a log is what to read there, "
                 "and which environments it belongs to. One connection serves every "
                 "log on that machine. Works with any backend - the format presets "
                 "are named after the shape of a line, and \"custom\" takes your own "
                 "patterns.")
        column.addWidget(self.wording.text(
            widgets.lede(""),
            "Backend logs a run can stream into each session's panel. A " + _tail,
            "Backend logs --server-log can stream into each session's panel. A " + _tail))
        column.addSpacing(12)

        self.add_connection_button = QPushButton("+ Add connection")
        self.add_connection_button.clicked.connect(self.add_connection)
        column.addWidget(widgets.row(widgets.kicker("Connections"), None,
                                     self.add_connection_button))
        self.connections = self._table(self.CONN_HEADERS, self.C_ACTIONS,
                                       stretch=self.C_WHERE)
        self.connections.doubleClicked.connect(
            lambda index: self.edit_connection(index.row()))
        column.addWidget(self._panel(self.connections), 1)
        column.addSpacing(10)

        self.open_tail_button = QPushButton("Open Tail")
        self.open_tail_button.clicked.connect(lambda: self.open_selected_log(False))
        self.open_tail_button.setToolTip("The last few hundred lines of the "
                                         "selected log, read through the launcher.")
        self.open_full_button = QPushButton("Open Full")
        self.open_full_button.clicked.connect(lambda: self.open_selected_log(True))
        self.open_full_button.setToolTip("As much of the selected log as is sane "
                                         "to move and to render.")
        self.add_log_button = QPushButton("+ Add log")
        self.add_log_button.clicked.connect(self.add_log)
        column.addWidget(widgets.row(widgets.kicker("Logs"), None,
                                     self.open_full_button, self.open_tail_button,
                                     self.add_log_button))
        self.logs = self._table(self.LOG_HEADERS, self.L_ACTIONS,
                                stretch=self.L_READS)
        self.logs.doubleClicked.connect(lambda index: self.edit_log(index.row()))
        column.addWidget(self._panel(self.logs), 2)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        column.addWidget(self.status)
        self._validate()

    # -- construction helpers -------------------------------------------------
    def _table(self, headers, actions_col, stretch):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        # Read-only: every edit goes through a dialog, so a cell that looks
        # editable and is not would be worse than one that plainly is not.
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(stretch, QHeaderView.Stretch)
        header.setSectionResizeMode(actions_col, QHeaderView.Fixed)
        table.setColumnWidth(actions_col, 180)
        return table

    @staticmethod
    def _panel(table):
        panel = widgets.BlueprintPanel(padding=(1, 1, 1, 1))
        panel.layout().addWidget(table)
        return panel

    # -- wiring ---------------------------------------------------------------
    def set_core(self, core):
        """The core runner, for Test. Without one the button reports as much."""
        self._core = core

    def set_environments(self, envs):
        """The env values from --describe, so a log is bound by picking not typing."""
        self._envs = [e for e in envs if e]

    def set_developer_mode(self, enabled):
        # Kept as well as applied: the row dialogs are built when they open,
        # and they have to open in the register the rest of the page is in.
        self._developer = bool(enabled)
        self.wording.apply(enabled)

    # -- loading / saving -----------------------------------------------------
    def load(self, path):
        self._path = path or ""
        self.path_label.setText(self._path or "(no log sources path configured)")
        self._load_error = ""
        try:
            self._connections, self._logs = lsf.load(self._path)
        except lsf.LogSourcesFileError as exc:
            self._connections, self._logs = [], []
            self._load_error = str(exc)
        self._fingerprint = lsf.fingerprint(self._path)
        self._rebuild()
        self._validate()

    def save(self):
        if not self._path:
            self._show_problem("No log sources path configured.")
            return
        current = lsf.fingerprint(self._path)
        if self._fingerprint is not None and current != self._fingerprint:
            answer = QMessageBox.question(
                self, "File changed on disk",
                "%s changed since it was loaded here.\n\nOverwrite it with what is "
                "on screen?" % self._path,
                QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Save:
                return
        try:
            lsf.save(self._path, self._connections, self._logs)
        except lsf.LogSourcesFileError as exc:
            self._show_problem(str(exc))
            return
        self._fingerprint = lsf.fingerprint(self._path)
        self._show_ok("Saved %d connection(s) and %d log(s) to %s (previous kept "
                      "as .bak)." % (len(self._connections), len(self._logs),
                                     self._path))
        self.saved.emit()

    # -- connections ----------------------------------------------------------
    def add_connection(self):
        dialog = ConnectionDialog(taken=[c.name for c in self._connections],
                                  parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._connections.append(dialog.value())
            self._rebuild()
            self._validate()

    def edit_connection(self, index):
        if not (0 <= index < len(self._connections)):
            return
        previous = self._connections[index].name
        dialog = ConnectionDialog(self._connections[index],
                                  taken=[c.name for c in self._connections],
                                  parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._connections[index] = dialog.value()
        # Logs refer to a connection by name; renaming one behind their back
        # would orphan every log that used it.
        renamed = self._connections[index].name
        if previous and renamed != previous:
            for log_row in self._logs:
                if log_row.connection == previous:
                    log_row.connection = renamed
        self._rebuild()
        self._validate()

    def duplicate_connection(self, index):
        if not (0 <= index < len(self._connections)):
            return
        clone = self._connections[index].copy()
        clone.name = self._unique(clone.name, [c.name for c in self._connections])
        self._connections.insert(index + 1, clone)
        self._rebuild()
        self._validate()

    def delete_connection(self, index):
        if not (0 <= index < len(self._connections)):
            return
        row = self._connections[index]
        used = [log.name for log in self._logs if log.connection == row.name]
        extra = ("\n\n%d log(s) use it: %s. They will have no connection until you "
                 "point them somewhere else." % (len(used), ", ".join(used))
                 if used else "")
        if QMessageBox.question(
                self, "Delete connection",
                "Remove connection %r?%s" % (row.name or "(unnamed)", extra),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        del self._connections[index]
        self._rebuild()
        self._validate()

    # -- logs -----------------------------------------------------------------
    def add_log(self):
        if not self._connections:
            self._show_problem("Add a connection first - a log has to be read over "
                               "one.")
            return
        seed = lsf.LogRow(connection=self._connections[0].name,
                          envs=self._envs[:1], type="file")
        dialog = LogDialog(seed, connections=self._connections, envs=self._envs,
                           siblings=self._logs, developer=self._developer,
                           parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._logs.append(dialog.value())
            self._rebuild()
            self._validate()

    def edit_log(self, index):
        if not (0 <= index < len(self._logs)):
            return
        others = [row for i, row in enumerate(self._logs) if i != index]
        dialog = LogDialog(self._logs[index], connections=self._connections,
                           envs=self._envs, siblings=others,
                           developer=self._developer, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self._logs[index] = dialog.value()
            self._rebuild()
            self._validate()

    def duplicate_log(self, index):
        if not (0 <= index < len(self._logs)):
            return
        clone = self._logs[index].copy()
        clone.name = self._unique(clone.name, [row.name for row in self._logs])
        self._logs.insert(index + 1, clone)
        self._rebuild()
        self._validate()

    def delete_log(self, index):
        if not (0 <= index < len(self._logs)):
            return
        row = self._logs[index]
        if QMessageBox.question(
                self, "Delete log", "Remove log %r?" % (row.name or "(unnamed)"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        del self._logs[index]
        self._rebuild()
        self._validate()

    @staticmethod
    def _unique(name, taken):
        """A copy's name, so a duplicate is valid the moment it is made."""
        candidate, n = "%s-copy" % (name or "log"), 2
        while candidate in taken:
            candidate, n = "%s-copy-%d" % (name or "log", n), n + 1
        return candidate

    # -- rendering ------------------------------------------------------------
    def _rebuild(self):
        self._rebuild_connections()
        self._rebuild_logs()

    def _rebuild_connections(self):
        table = self.connections
        table.setRowCount(len(self._connections))
        for index, row in enumerate(self._connections):
            used = sum(1 for log in self._logs if log.connection == row.name)
            self._set(table, index, self.C_NAME, row.name or "(unnamed)", mono=True)
            self._set(table, index, self.C_WHERE, row.describe(), mono=True)
            self._set(table, index, self.C_USED,
                      "%d log%s" % (used, "" if used == 1 else "s"))
            table.setCellWidget(index, self.C_ACTIONS, self._actions(
                self.edit_connection, self.duplicate_connection,
                self.delete_connection, index))
        self._fit_columns(table, self.C_ACTIONS)

    def _rebuild_logs(self):
        table = self.logs
        table.setRowCount(len(self._logs))
        for index, row in enumerate(self._logs):
            self._set(table, index, self.L_NAME, row.name or "(unnamed)", mono=True)
            self._set(table, index, self.L_ENVS, row.envs_text() or "(none)",
                      mono=True)
            self._set(table, index, self.L_READS,
                      "%s  %s" % (row.type, row.target or "—"), mono=True)
            self._set(table, index, self.L_CONN, row.connection or "(none)",
                      mono=True)
            self._set(table, index, self.L_FORMAT, row.format_label())
            self._set(table, index, self.L_DEFAULT, "yes" if row.default else "")
            table.setCellWidget(index, self.L_ACTIONS, self._actions(
                self.edit_log, self.duplicate_log, self.delete_log, index))
        self._fit_columns(table, self.L_ACTIONS)

    def _actions(self, edit, duplicate, delete, index):
        buttons = []
        for label, handler in (("Edit", edit), ("Copy", duplicate),
                               ("Delete", delete)):
            button = QPushButton(label)
            button.setProperty("variant", "ghost")
            button.clicked.connect(lambda _c=False, h=handler, i=index: h(i))
            buttons.append(button)
        return widgets.row(*buttons, None, spacing=2)

    @staticmethod
    def _fit_columns(table, actions_col, cap=360):
        """Size to content, but never let one long value squeeze out the rest.

        A name or a path can be arbitrarily long, and sizing purely to content
        hands it the whole width - leaving the column that says what the row
        actually reads truncated to an ellipsis. The full value is still there on
        hover and in the dialog.
        """
        table.resizeColumnsToContents()
        for index in range(table.columnCount()):
            if index == actions_col:
                continue
            table.setColumnWidth(index, min(table.columnWidth(index), cap))
        table.setColumnWidth(actions_col, 180)

    def _set(self, table, row, col, text, mono=False):
        item = QTableWidgetItem(text)
        if mono:
            item.setFont(theme.mono_font(9))
        item.setToolTip(text)      # columns are capped, so a long value can clip
        table.setItem(row, col, item)

    # -- opening a log --------------------------------------------------------
    def open_selected_log(self, whole):
        """Show what the selected log holds - the whole of it, or its tail."""
        index = self.logs.currentRow()
        if not (0 <= index < len(self._logs)):
            self._show_problem("Select a log to open.")
            return
        row = self._logs[index]
        if not row.name:
            self._show_problem("Give the log a name first.")
            return
        if self._core is None:
            self._show_problem("No core configured (Settings).")
            return
        if lsf.validate(self._connections, self._logs):
            self._show_problem("Fix the problems below and save first - the "
                               "launcher reads the file, not the screen.")
            return
        if self._fingerprint != lsf.fingerprint(self._path):
            self._show_problem("Save first: the launcher reads what is in the file.")
            return
        self._show_ok("Opening %s…" % row.name)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self._core.server_log_show(row.name,
                                                lines=None if whole else 500)
        finally:
            QApplication.restoreOverrideCursor()
        if not result.get("ok"):
            self._show_problem("%s: %s" % (row.name, result.get("error")
                                           or "could not be read"))
            return
        self._show_ok("%s: %d line(s) from %s" % (row.name,
                                                  len(result.get("lines") or []),
                                                  result.get("target", "")))
        self._show_viewer(row.name, result, whole)

    def _show_viewer(self, name, result, whole):
        """Open the viewer, non-modally, and keep it alive.

        Not modal: comparing two logs, or reading one while fixing the config that
        points at it, is the normal thing to want. A window with no reference held
        would be collected the moment this returns, so they are kept until closed.
        """
        viewer = LogViewerDialog(name, result, whole, parent=self)
        self._viewers.append(viewer)
        viewer.finished.connect(lambda _code, v=viewer: self._viewers.remove(v)
                                if v in self._viewers else None)
        viewer.show()
        viewer.raise_()
        return viewer

    # -- validation -----------------------------------------------------------
    def _validate(self):
        if self._load_error:
            # Never offer to save over a file that could not be read: an empty
            # editor validates perfectly, and saving it would throw the content
            # away. Reload once the file is fixed.
            self._show_problem("%s\n\nFix the file and press Reload - saving now "
                               "would replace it with an empty one."
                               % self._load_error)
            self.save_button.setEnabled(False)
            return False
        problems = lsf.validate(self._connections, self._logs)
        if problems:
            self._show_problem("\n".join(problems[:6]))
        elif not self._connections and not self._logs:
            self._show_ok("Nothing configured yet. Add a connection, then a log on "
                          "it - there is nothing to stream until you do.")
        else:
            self._show_ok("%d connection(s) · %d log(s) · every log has a connection, "
                          "an environment and a target · names unique per environment"
                          % (len(self._connections), len(self._logs)))
        self.save_button.setEnabled(not problems)
        return not problems

    def _show_problem(self, message):
        self.status.setText(message)
        self.status.setStyleSheet("color: %s; font-size: 12px;" % theme.BAD)

    def _show_ok(self, message):
        self.status.setText(message)
        self.status.setStyleSheet("color: %s; font-size: 12px;" % theme.NEUTRAL[700])

    def rows(self):
        return list(self._connections), list(self._logs)
