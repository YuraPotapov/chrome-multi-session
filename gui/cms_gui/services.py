"""Starting, stopping and watching the services on the Services & Logs page.

The counterpart of :mod:`~cms_gui.runnertypes`: that module says what the command
lines *are*, this one runs them. Everything is signal-driven on the Qt event loop,
the same rule :mod:`~cms_gui.runner` follows for the launcher - a service that
hangs on startup leaves the window perfectly responsive.

There are three execution shapes, and the difference between them is not a detail:

* **Supervised, attached** - a :class:`QProcess` we own. Its state *is* the
  process's state, its output arrives on a pipe, and stopping it means signalling
  it. This is the only shape whose exit code we ever learn.
* **Supervised, detached** - the same command, but it has to outlive this window.
* **Managed** - ``docker start`` and friends. The command exits the moment the
  daemon has taken the job, so what it tells us is whether the *request* worked,
  never whether the thing is up. That has to be asked separately, which is what
  the poll below is for.

Two facts about PySide6 6.11.1 shaped the detached shape, and both were measured
rather than assumed:

1. ``QProcess`` kills a still-running child in its destructor, so a service that
   must survive the window cannot be an attached ``QProcess`` at all.
2. ``QProcess.startDetached()`` is annotated ``-> Tuple[bool, int]`` but returns a
   bare ``bool`` from the instance method, so it hands back no pid; the static
   overload does return one, but takes neither an environment nor a redirection.

So a detached service is started with ``subprocess.Popen`` - which gives a pid, a
working directory, an environment and a redirection in one call, and which,
unlike ``QProcess``, does not kill anything when it is collected. Its output goes
straight to a file and the console reads that file. Attached services write the
same file as their lines arrive, so one viewer serves both and a service's output
outlives the window that was watching it.

Nothing here imports the engine, ssh, docker or yaml - it builds argument lists
and runs them, which is what the GUI has always been allowed to do.
"""

import collections
import os
import re
import signal
import subprocess

from PySide6.QtCore import (QObject, QProcess, QProcessEnvironment, QTimer,
                            Signal)

from . import criteria as criteria_mod
from . import runnertypes, store
from .runnertypes import (FAILED, MANAGED, RUNNING, STARTING, STOPPED, STOPPING,
                          WAITING)

#: Console lines kept in memory per service. A chatty service would otherwise
#: grow the model without bound for as long as the window is open; the file on
#: disk keeps the rest.
CONSOLE_LINES = 2000

#: Rotate a service's log at this size, checked when it starts rather than while
#: it runs - a rotation mid-stream would cut a line in half.
LOG_MAX_BYTES = 5 * 1024 * 1024

#: How often the managed services and the detached pids are asked how they are.
#: Attached services need no poll at all: they tell us.
POLL_MS = 3000

#: How long a signalled service gets to go down on its own before it is killed.
STOP_GRACE_MS = 8000

#: What ``ServiceSupervisor.shutdown`` waits, per service, on the way out.
SHUTDOWN_WAIT_MS = 10000

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slug(project, name):
    """A filename-safe stand-in for one service's (project, name) pair."""
    text = "%s-%s" % (project or "project", name or "service")
    return _UNSAFE.sub("_", text).strip("_")[:120] or "service"


def services_log_dir(directory=None):
    """Where service output is kept - beside the run logs, not among them."""
    path = os.path.join(store.logs_dir(directory), "services")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def rotate(path, limit=LOG_MAX_BYTES):
    """Move an oversized log aside, keeping one previous copy. True if it moved."""
    try:
        if os.path.getsize(path) < limit:
            return False
    except OSError:
        return False
    previous = path + ".1"
    try:
        if os.path.exists(previous):
            os.remove(previous)
        os.replace(path, previous)
    except OSError:
        return False
    return True


class LogTail:
    """Whole new lines appended to a file since the last look.

    A detached service writes straight to disk and there is no pipe to listen to,
    so the console has to go and read. Keeping only an offset means a long log
    costs one seek per poll rather than a re-read; a file that has *shrunk* was
    rotated or truncated underneath us, and the only sane response is to start
    again from its top rather than to seek past its end forever.
    """

    def __init__(self, path):
        self.path = path
        self._offset = 0
        self._partial = ""

    def rewind(self):
        self._offset = 0
        self._partial = ""

    def seek_end(self):
        """Ignore everything already there - used when a new run is starting."""
        try:
            self._offset = os.path.getsize(self.path)
        except OSError:
            self._offset = 0
        self._partial = ""

    def read(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self._offset:
            self.rewind()
        if size == self._offset:
            return []
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self._offset)
                data = handle.read(size - self._offset)
        except OSError:
            return []
        self._offset += len(data)
        self._partial += data.decode("utf-8", "replace")
        if "\n" not in self._partial:
            return []
        lines = self._partial.split("\n")
        self._partial = lines.pop()
        return [line.rstrip("\r") for line in lines]


def pid_alive(pid, marker=""):
    """Is this pid still a running process - and still *our* process?

    ``marker`` is the program we started, checked against the process's own
    command line where the system will say. A pid is reused eventually, and
    reporting somebody else's shell as a running Odoo would be worse than
    reporting nothing.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True           # alive, and not ours to signal
    except OSError:
        return False
    if not marker:
        return True
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            command = handle.read().decode("utf-8", "replace")
    except OSError:
        return True           # no /proc to ask (macOS, BSD): take the pid's word
    return os.path.basename(marker) in command


def _alive_windows(pid):
    """Ask the kernel directly rather than spawning tasklist once every poll."""
    try:
        import ctypes

        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def terminate_pid(pid):
    """Ask a detached service to go down. The poll confirms that it did."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        # /T because a detached service was given its own process group and its
        # children are in it. Fire and forget: the poll is what reports the
        # outcome, so this must not block the event loop.
        QProcess.startDetached("taskkill", ["/PID", str(pid), "/T"])
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def _environ(settings):
    """The child's environment: ours, plus whatever the runner adds."""
    values = dict(os.environ)
    for key, value in (settings.get("env") or {}).items():
        if str(key).strip():
            values[str(key)] = str(value)
    return values


def _qt_environ(settings):
    environment = QProcessEnvironment.systemEnvironment()
    for key, value in (settings.get("env") or {}).items():
        if str(key).strip():
            environment.insert(str(key), str(value))
    return environment


class DetachedState:
    """The pids of services that outlive the window, so they can be found again.

    Runtime state, not configuration, so it lives in the GUI's own data directory
    rather than in ``services.json``: a pid is true for one boot of one machine
    and has no business in a file somebody might copy to another.
    """

    def __init__(self, path=None):
        self._store = store.JsonStore(
            path or os.path.join(store.app_data_dir(), "services-state.json"),
            default={})

    def all(self):
        value = self._store.load()
        return {k: v for k, v in value.items() if isinstance(v, dict)}

    def get(self, key, project, name):
        entry = self.all().get(key)
        if not isinstance(entry, dict):
            return None
        # The slug is derived from the pair, but two pairs can slug the same;
        # checking both is cheaper than making the key unreadable.
        if entry.get("project") != project or entry.get("name") != name:
            return None
        return entry

    def remember(self, key, project, name, pid, marker):
        entries = self.all()
        entries[key] = {"project": project, "name": name, "pid": int(pid),
                        "marker": marker}
        self._store.save(entries)

    def forget(self, key):
        entries = self.all()
        if entries.pop(key, None) is not None:
            self._store.save(entries)


class ServiceProcess(QObject):
    """One configured service, and whatever is currently true about it."""

    status_changed = Signal(str)
    output = Signal(str)
    #: One or more of its criteria lit or went dark. Separate from status on
    #: purpose: a criterion says what the log says, status says what the process
    #: is doing, and neither has any business moving the other.
    criteria_changed = Signal()

    def __init__(self, project, runner, project_dir="", state=None, parent=None):
        super().__init__(parent)
        self.project = project
        self.runner = runner
        self.project_dir = project_dir
        self.slug = slug(project, runner.name)
        self.log_path = os.path.join(services_log_dir(), self.slug + ".log")
        #: True when the configuration changed while this was running, so the
        #: row can say that what is up is not what is written down.
        self.stale = False

        self._state = state
        self._status = STOPPED
        self._detail = ""
        self._proc = None          # attached child
        self._action = None        # a managed start/stop, in flight
        self._probe = None         # a managed status query, in flight
        self._probe_program = ""   # what that query runs, for its error message
        self._pid = 0              # detached child
        self._marker = ""
        self._stopping = False
        self._restart = False
        self._out_buffer = ""
        self._handle = None
        self._tail = LogTail(self.log_path)
        self._lines = collections.deque(maxlen=CONSOLE_LINES)
        self._matchers = []
        #: path -> LogTail, for criteria that read a file rather than this
        #: service's own output. One tail per distinct path, however many
        #: criteria share it.
        self._watched = {}
        self._build_matchers()

    # -- what it is -----------------------------------------------------------
    @property
    def key(self):
        return (self.project, self.runner.name)

    @property
    def name(self):
        return self.runner.name

    @property
    def status(self):
        return self._status

    @property
    def detail(self):
        return self._detail

    def kind(self):
        return self.runner.runner_type

    def is_managed(self):
        kind = self.kind()
        return kind is not None and kind.mode == MANAGED

    def is_detached(self):
        return bool(self.runner.detach) and not self.is_managed()

    def is_running(self):
        # WAITING counts: it has been asked to start and is on its way there, so
        # Start must not be offered again and Stop must be.
        return self._status in (WAITING, STARTING, RUNNING, STOPPING)

    def is_waiting(self):
        return self._status == WAITING

    def mark_waiting(self, detail):
        """Asked to start, but something it depends on is not up yet."""
        self._set_status(WAITING, detail)

    def cancel_waiting(self, detail=""):
        """Give up on waiting - the dependency failed, or Stop was pressed."""
        if self._status == WAITING:
            self._restart = False
            self._set_status(STOPPED, detail)
            return True
        return False

    def detained(self):
        """Running, and ours to lose when the window closes."""
        return self.is_running() and not self.runner.detach and not self.is_managed()

    def console(self):
        return list(self._lines)

    def needs_poll(self):
        # Also when a criterion reads a file: that is the only thing that goes
        # and looks at it, and an attached service has nothing else to poll for.
        return self.is_managed() or bool(self._pid) or bool(self._watched)

    def update(self, runner, project_dir):
        """Take the edited configuration. What is already up keeps its own."""
        was = (self.runner.type, self.runner.settings, self.runner.detach)
        criteria_was = [one.to_entry() for one in self.runner.criteria]
        self.runner = runner
        self.project_dir = project_dir
        if criteria_was != [one.to_entry() for one in runner.criteria]:
            self._build_matchers()
            # Answer for what it has already printed rather than only for what it
            # says next - on a quiet service that could be never.
            for matcher in self._matchers:
                if not matcher.source:
                    matcher.feed_all(self._lines)
            self.criteria_changed.emit()
        if self.is_running() and was != (runner.type, runner.settings, runner.detach):
            self.stale = True
            self.status_changed.emit(self._status)

    # -- criteria -------------------------------------------------------------
    def _build_matchers(self):
        """Fresh matchers for the configured criteria, and tails for their files."""
        self._matchers = criteria_mod.matchers_for(self.runner.criteria)
        wanted = criteria_mod.sources_of(self.runner.criteria)
        # Keyed by what the criterion says, opened at what that means: a matcher
        # is found by comparing its own source string, but `~/logs/app.log` is
        # not a path anything can read.
        self._watched = {path: self._watched.get(path)
                                or LogTail(os.path.expanduser(path))
                         for path in wanted}

    def _reset_criteria(self):
        """Forget the last run, so the column describes this one."""
        for matcher in self._matchers:
            matcher.reset()
        for tail in self._watched.values():
            # Only what this run writes: what the file already holds belongs to
            # whatever wrote it last, which is usually the previous run.
            tail.seek_end()
        if self._matchers:
            self.criteria_changed.emit()

    def _feed_criteria(self, line, source=""):
        """Give one line to every criterion reading that source."""
        changed = False
        for matcher in self._matchers:
            if matcher.source == source:
                changed = matcher.feed(line) or changed
        if changed:
            self.criteria_changed.emit()

    def _drain_watched(self):
        """Read whatever the watched files have gained since the last look."""
        for path, tail in self._watched.items():
            for line in tail.read():
                self._feed_criteria(line, path)

    def criteria_state(self):
        """(name, colour, lit, why-not) per criterion, for the page to draw."""
        return [(matcher.name, matcher.color, matcher.lit(), matcher.outstanding())
                for matcher in self._matchers]

    # -- lifecycle ------------------------------------------------------------
    def start(self):
        # Not is_running(): a service that has been WAITING for its dependencies
        # is started from exactly that state, and this is the call that does it.
        if self._status in (STARTING, RUNNING, STOPPING):
            return False
        kind = self.kind()
        if kind is None:
            return self._fail("Unknown runner type %r - this build does not have it."
                              % self.runner.type)
        try:
            cwd = runnertypes.resolve_dir(self.runner.settings, self.project_dir)
            argv = [str(part) for part in kind.command(self.runner.settings, cwd)]
        except ValueError as exc:
            return self._fail(str(exc))
        if not argv or not argv[0].strip():
            return self._fail("Nothing to run - check the configuration.")
        if cwd and not os.path.isdir(cwd):
            return self._fail("Working directory does not exist: %s" % cwd)
        self.stale = False
        self._detail = ""
        self._stopping = False
        self._reset_criteria()
        self._set_status(STARTING)
        if kind.mode == MANAGED:
            self._run_action(argv, cwd, starting=True)
        elif self.runner.detach:
            self._start_detached(argv, cwd)
        else:
            self._start_attached(argv, cwd)
        return True

    def stop(self):
        # Nothing was ever spawned, so there is nothing to signal - stopping a
        # service that is still waiting is simply giving up on the wait.
        if self.cancel_waiting():
            return True
        kind = self.kind()
        if kind is not None and kind.mode == MANAGED:
            if self._action is not None:
                return False
            stop_argv = kind.stop_command(self.runner.settings)
            if not stop_argv:
                return False
            self._set_status(STOPPING)
            self._run_action([str(p) for p in stop_argv], "", starting=False)
            return True
        if self._pid:
            self._set_status(STOPPING)
            terminate_pid(self._pid)
            return True
        if self._proc is None or self._proc.state() == QProcess.NotRunning:
            return False
        self._stopping = True
        self._set_status(STOPPING)
        self._signal_attached()
        return True

    def restart(self):
        if not self.is_running():
            return self.start()
        self._restart = True
        return self.stop()

    def _signal_attached(self):
        """SIGINT first: a service that handles it flushes and closes cleanly."""
        pid = int(self._proc.processId() or 0)
        if os.name == "nt" or not pid:
            self._proc.terminate()
        else:
            try:
                os.kill(pid, signal.SIGINT)
            except OSError:
                self._proc.terminate()
        QTimer.singleShot(STOP_GRACE_MS, self._kill_if_still_running)

    def _kill_if_still_running(self):
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    # -- the three shapes -----------------------------------------------------
    def _start_attached(self, argv, cwd):
        rotate(self.log_path)
        self._open_log()
        proc = QProcess(self)
        # Merged, unlike the launcher's: a service's stderr is part of its log,
        # not a separate channel meaning something else.
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read_output)
        proc.started.connect(lambda: self._set_status(RUNNING))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        if cwd:
            proc.setWorkingDirectory(cwd)
        proc.setProcessEnvironment(_qt_environ(self.runner.settings))
        self._proc = proc
        self._marker = argv[0]
        proc.start(argv[0], list(argv[1:]))

    def _start_detached(self, argv, cwd):
        rotate(self.log_path)
        # Only this run's output belongs in the console; what the file already
        # holds is the previous one's and is still there to be opened.
        self._tail.seek_end()
        kwargs = {"stdout": None, "stderr": subprocess.STDOUT,
                  "stdin": subprocess.DEVNULL, "cwd": cwd or None,
                  "env": _environ(self.runner.settings)}
        if os.name == "nt":
            DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP = 0x00000008, 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            # Its own session, so the SIGINT that stops this application never
            # reaches a service that was asked to outlive it.
            kwargs["start_new_session"] = True
        try:
            handle = open(self.log_path, "a", encoding="utf-8")
        except OSError as exc:
            self._fail("Cannot write %s: %s" % (self.log_path, exc))
            return
        try:
            with handle:
                kwargs["stdout"] = handle
                child = subprocess.Popen(argv, **kwargs)
        except OSError as exc:
            self._fail("Could not start %s: %s" % (argv[0], exc))
            return
        self._pid = int(child.pid)
        self._marker = argv[0]
        if self._state is not None:
            self._state.remember(self.slug, self.project, self.runner.name,
                                 self._pid, self._marker)
        self._set_status(RUNNING)

    def _run_action(self, argv, cwd, starting):
        """One managed command - `docker start`, `docker compose down`.

        Its exit code says whether the request was accepted. Whether the thing is
        actually up is a separate question, asked by the probe once this returns.
        """
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read_output)
        proc.finished.connect(lambda code, _s, up=starting:
                              self._action_finished(code, up))
        proc.errorOccurred.connect(self._on_error)
        if cwd:
            proc.setWorkingDirectory(cwd)
        self._action = proc
        self._proc = proc
        self._marker = argv[0]
        rotate(self.log_path)
        self._open_log()
        proc.start(argv[0], list(argv[1:]))

    def _action_finished(self, code, starting):
        self._read_output()
        self._flush_output()
        self._close_log()
        self._action = None
        self._proc = None
        if code != 0:
            self._fail("`%s` failed (exit %s). %s"
                       % (self._marker, code, self._last_line()))
            return
        if self._restart and not starting:
            # The stop command returned 0, so it is down; probing first and then
            # starting is a slower road to the same place, and start() refuses
            # while the status is still "stopping" - which is what it would be
            # until that probe answered.
            self._restart = False
            self._set_status(STOPPED, "")
            QTimer.singleShot(0, self.start)
            return
        # Accepted. Now ask what is actually true, rather than assume.
        self.poll(force=True)

    # -- output ---------------------------------------------------------------
    def _open_log(self):
        try:
            self._handle = open(self.log_path, "a", encoding="utf-8")
        except OSError:
            self._handle = None

    def _close_log(self):
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

    def _read_output(self):
        source = self._proc
        if source is None:
            return
        data = bytes(source.readAllStandardOutput()).decode("utf-8", "replace")
        if not data:
            return
        self._out_buffer += data
        if "\n" not in self._out_buffer:
            return
        lines = self._out_buffer.split("\n")
        self._out_buffer = lines.pop()
        for line in lines:
            self._emit_line(line.rstrip("\r"))

    def _flush_output(self):
        if self._out_buffer.strip():
            self._emit_line(self._out_buffer.rstrip("\r\n"))
        self._out_buffer = ""

    def _emit_line(self, line, to_file=True):
        """One line of this service's own output, from whichever shape produced it.

        The single door, which is what makes the criteria correct rather than
        nearly correct: a detached service's lines used to be appended and emitted
        beside this rather than through it, so nothing watching output would ever
        have seen them. ``to_file`` is False for those - the child wrote the file
        itself, and writing it again here would double every line.
        """
        self._lines.append(line)
        if to_file and self._handle is not None:
            try:
                self._handle.write(line + "\n")
                self._handle.flush()
            except (OSError, ValueError):
                self._handle = None
        self._feed_criteria(line)
        self.output.emit(line)

    def _last_line(self):
        for line in reversed(self._lines):
            if line.strip():
                return line.strip()
        return ""

    def _drain_tail(self):
        for line in self._tail.read():
            self._emit_line(line, to_file=False)

    # -- the attached child's own reports -------------------------------------
    def _on_finished(self, code, _status):
        self._read_output()
        self._flush_output()
        self._close_log()
        self._proc = None
        if self._stopping or code == 0:
            self._stopping = False
            self._set_status(STOPPED, "")
        else:
            self._set_status(FAILED, "exited with code %s. %s"
                             % (code, self._last_line()))
        if self._restart:
            self._restart = False
            QTimer.singleShot(0, self.start)

    def _on_error(self, error):
        if error != QProcess.FailedToStart:
            return
        program = self._proc.program() if self._proc is not None else self._marker
        self._close_log()
        self._proc = None
        self._action = None
        self._restart = False
        self._set_status(FAILED, "Could not start %s - is it installed and on "
                                 "PATH?" % program)

    # -- polling --------------------------------------------------------------
    def poll(self, force=False):
        """Ask the shapes that cannot tell us themselves how they are."""
        # Before the early returns below, which are about pids and containers:
        # a criterion reading a file is polled whatever shape the service is.
        self._drain_watched()
        if self._pid:
            self._drain_tail()
            if not pid_alive(self._pid, self._marker):
                self._forget_pid()
                self._set_status(STOPPED, "")
                # A detached service reports nothing when it goes, so a restart
                # of one can only be finished here - the poll is the only place
                # that ever learns it is down.
                if self._restart:
                    self._restart = False
                    QTimer.singleShot(0, self.start)
            return
        if not self.is_managed():
            return
        if self._action is not None and not force:
            # A `docker stop` takes its time, and a probe answered mid-way would
            # flip the row back to Running while it is plainly going down.
            return
        if self._probe is not None:
            return            # last one has not answered yet; do not pile up
        kind = self.kind()
        argv = kind.probe_command(self.runner.settings) if kind else None
        if not argv:
            return
        probe = QProcess(self)
        probe.setProcessChannelMode(QProcess.SeparateChannels)
        probe.finished.connect(lambda code, _s, p=probe: self._probe_finished(p, code))
        probe.errorOccurred.connect(lambda _e, p=probe: self._probe_failed(p))
        self._probe = probe
        self._probe_program = str(argv[0])
        probe.start(self._probe_program, [str(part) for part in argv[1:]])

    def _probe_finished(self, probe, code):
        if probe is not self._probe:
            return
        try:
            out = bytes(probe.readAllStandardOutput()).decode("utf-8", "replace")
        except RuntimeError:
            self._probe = None      # disposed underneath us; nothing to read
            return
        self._probe = None
        probe.deleteLater()
        kind = self.kind()
        if kind is None:
            return
        answer = kind.probe_reads(out, int(code))
        if self._action is not None:
            return                     # a command is in flight; it will re-ask
        if answer == RUNNING:
            self._set_status(RUNNING, "")
        elif self._status != FAILED:
            # A failure keeps its reason until something actually starts. The
            # probe of a container that would not start says "stopped", which is
            # true and useless: three seconds after `docker start` reported why
            # it failed, the row would have replaced that with nothing at all.
            self._set_status(STOPPED, "")

    def _probe_failed(self, probe):
        if probe is not self._probe:
            return
        self._probe = None
        try:
            probe.deleteLater()
        except RuntimeError:
            return                  # disposed underneath us
        # The probe command itself could not run. Say which one and why, rather
        # than reporting the service as stopped every three seconds forever.
        self._set_status(FAILED, "Could not run the status check - is %s "
                                 "installed and on PATH?"
                                 % (self._probe_program or "it"))

    def dispose(self):
        """Let go of everything in flight, before this object is deleted.

        A probe or a `docker stop` outliving the service that started it is not
        theoretical: removing a project from the page while its status check is
        on the wire left Qt destroying a running QProcess and a signal arriving
        at a C++ object Python had already dropped.
        """
        for proc in (self._probe, self._action):
            if proc is None:
                continue
            try:
                proc.blockSignals(True)
                if proc.state() != QProcess.NotRunning:
                    proc.kill()
                    proc.waitForFinished(1000)
            except RuntimeError:
                pass
        self._probe = None
        self._action = None
        self._close_log()

    def _forget_pid(self):
        if self._state is not None:
            self._state.forget(self.slug)
        self._pid = 0

    # -- adoption -------------------------------------------------------------
    def reattach(self):
        """Pick up what is already running: a detached pid, or a live container."""
        if self.is_managed():
            self.poll(force=True)
            return
        if self._state is None or not self.runner.detach:
            return
        entry = self._state.get(self.slug, self.project, self.runner.name)
        if not entry:
            return
        pid, marker = entry.get("pid"), entry.get("marker") or ""
        if not pid_alive(pid, marker):
            self._state.forget(self.slug)
            return
        self._pid = int(pid)
        self._marker = marker
        self._tail.seek_end()
        self._set_status(RUNNING, "started before this window was opened")

    # -- status ---------------------------------------------------------------
    def _fail(self, message):
        self._set_status(FAILED, message)
        return False

    def _set_status(self, status, detail=None):
        if detail is not None:
            self._detail = detail
        if status == self._status:
            return
        self._status = status
        # Once nothing is running there is nothing for the log to be saying. A
        # green "start" beside a stopped service is a claim about a run that is
        # over, and beside a failed one it contradicts the row it sits in.
        if status in (STOPPED, FAILED):
            self._reset_criteria()
        self.status_changed.emit(status)

    def wait_for_stop(self, timeout_ms=SHUTDOWN_WAIT_MS):
        """Block until the attached child is gone. Only for the way out."""
        if self._proc is None:
            return True
        return bool(self._proc.waitForFinished(timeout_ms))


class ServiceSupervisor(QObject):
    """Every configured service, kept in step with the configuration.

    Also the only thing that knows about **dependencies**, and deliberately so: a
    service waiting on another is a fact about the project, not about either
    process, and a ServiceProcess that had to look its siblings up would need a
    way to reach them. Here the ordering is read straight off the configuration.
    """

    status_changed = Signal(str, str, str)     # project, runner, status
    output = Signal(str, str, str)             # project, runner, line
    criteria_changed = Signal(str, str)        # project, runner

    def __init__(self, parent=None, state=None):
        super().__init__(parent)
        self._services = collections.OrderedDict()
        self._projects = []
        #: key -> the dependency keys it is still waiting on.
        self._waiting = {}
        #: keys stopped only so they can be started again once they are down.
        self._restarting = set()
        self._state = state if state is not None else DetachedState()
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)

    # -- the configuration ----------------------------------------------------
    def sync(self, projects):
        """Reconcile with the edited configuration, keeping what is up, up."""
        self._projects = list(projects)
        wanted = collections.OrderedDict()
        for project in projects:
            for runner in project.runners:
                if project.name and runner.name:
                    wanted[(project.name, runner.name)] = (project, runner)

        for key in list(self._services):
            if key in wanted:
                continue
            self._waiting.pop(key, None)
            service = self._services.pop(key)
            # A service deleted while it is still running: if it is ours and
            # attached, nothing would be able to stop it once its row is gone,
            # so stop it now. A detached one or a container was explicitly meant
            # to live without us, and deleting a row is not an instruction to
            # kill it.
            if service.detained():
                service.stop()
                service.wait_for_stop(3000)
            service.dispose()
            service.deleteLater()

        for key, (project, runner) in wanted.items():
            service = self._services.get(key)
            if service is None:
                service = ServiceProcess(project.name, runner, project.dir,
                                         state=self._state, parent=self)
                service.status_changed.connect(
                    lambda status, k=key: self._service_changed(k, status))
                service.output.connect(
                    lambda line, k=key: self.output.emit(k[0], k[1], line))
                service.criteria_changed.connect(
                    lambda k=key: self.criteria_changed.emit(k[0], k[1]))
                self._services[key] = service
                service.reattach()
            else:
                service.update(runner, project.dir)
        # Keep the page's order, so the tables and this agree about what is where.
        self._services = collections.OrderedDict(
            (key, self._services[key]) for key in wanted if key in self._services)
        self._retime()

    def _retime(self):
        if any(service.needs_poll() for service in self._services.values()):
            if not self._timer.isActive():
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()

    def _tick(self):
        for service in list(self._services.values()):
            service.poll()

    # -- dependencies ---------------------------------------------------------
    def _project(self, name):
        for project in self._projects:
            if project.name == name:
                return project
        return None

    def _service_changed(self, key, status):
        """One service moved; some other may have been waiting for exactly that."""
        if key in self._restarting and status in (STOPPED, FAILED):
            self._restarting.discard(key)
            # Not straight away: this is running inside the stopped service's own
            # signal, and starting from there re-enters a state machine that is
            # still finishing the previous transition.
            QTimer.singleShot(0, lambda k=key: self.start(k[0], k[1]))
        self._release(key, status)
        self.status_changed.emit(key[0], key[1], status)

    def _release(self, key, status):
        for waiter, pending in list(self._waiting.items()):
            if key not in pending:
                continue
            if status == RUNNING:
                pending.discard(key)
                if not pending:
                    self._waiting.pop(waiter, None)
                    service = self._services.get(waiter)
                    if service is not None and service.is_waiting():
                        service.start()
            elif status in (STOPPED, FAILED):
                # What it was waiting for is not coming. Saying so beats leaving
                # the row on "Waiting…" forever with nothing to explain it.
                self._waiting.pop(waiter, None)
                service = self._services.get(waiter)
                if service is not None and service.is_waiting():
                    service.cancel_waiting(
                        "%s did not start, so this never did either." % key[1])

    def _unmet(self, key):
        """The dependency keys of ``key`` that are not up yet, in start order."""
        project = self._project(key[0])
        runner = project.runner(key[1]) if project is not None else None
        if runner is None:
            return []
        unmet = []
        for name in runner.depends:
            dependency = self._services.get((key[0], name))
            if dependency is not None and dependency.status != RUNNING:
                unmet.append((key[0], name))
        return unmet

    # -- reading --------------------------------------------------------------
    def service(self, project, name):
        return self._services.get((project, name))

    def status(self, project, name):
        service = self._services.get((project, name))
        return service.status if service is not None else STOPPED

    def counts(self, project):
        """(running, total) for one project - the block header's summary."""
        rows = [s for key, s in self._services.items() if key[0] == project]
        return sum(1 for s in rows if s.status == RUNNING), len(rows)

    def detained(self):
        """The services that would be lost if the window closed now."""
        return [service for service in self._services.values() if service.detained()]

    def criteria_state(self, project, name):
        """What one service's criteria say right now. Empty when it has none."""
        service = self._services.get((project, name))
        return service.criteria_state() if service is not None else []

    def running(self):
        """Every service that is up, in the order the page shows them."""
        return [service for service in self._services.values()
                if service.status == RUNNING]

    # -- acting ---------------------------------------------------------------
    def start(self, project, name, _seen=None):
        """Start one service, and whatever it waits for, in the right order."""
        key = (project, name)
        service = self._services.get(key)
        if service is None or service.is_running():
            return False
        # A cycle is refused by servicesfile.validate, but a file edited by hand
        # reaches this before anyone presses Save - and recursing round a ring
        # would take the window with it.
        seen = _seen if _seen is not None else set()
        if key in seen:
            return False
        seen.add(key)

        unmet = self._unmet(key)
        if not unmet:
            started = service.start()
            self._retime()
            return started
        self._waiting[key] = set(unmet)
        service.mark_waiting(
            "waiting for %s" % ", ".join(sorted(other[1] for other in unmet)))
        for dependency in unmet:
            self.start(dependency[0], dependency[1], seen)
        self._retime()
        return True

    def stop(self, project, name):
        self._waiting.pop((project, name), None)
        service = self._services.get((project, name))
        return bool(service and service.stop())

    def restart(self, project, name):
        """Stop it, then start it again *through here*, so its waits still hold.

        ServiceProcess.restart would start it directly, which is right when it
        answers to nobody and wrong the moment it depends on something: the
        second half of a restart has to go back through :meth:`start` to find out
        whether what it waits for is still up.
        """
        key = (project, name)
        service = self._services.get(key)
        if service is None:
            return False
        if not service.is_running():
            return self.start(project, name)
        self._waiting.pop(key, None)
        self._restarting.add(key)
        if service.is_waiting():
            # Nothing was ever spawned; giving up the wait is the whole stop, and
            # it reports STOPPED synchronously, which starts it again below.
            service.cancel_waiting()
        else:
            service.stop()
        return True

    def _ordered(self, project=None):
        """Every service in dependency order - what has to be up first, first."""
        ordered = []
        for row in self._projects:
            if project not in (None, row.name):
                continue
            for runner in row.start_order():
                service = self._services.get((row.name, runner.name))
                if service is not None:
                    ordered.append(((row.name, runner.name), service))
        return ordered

    def start_all(self, project=None):
        for key, service in self._ordered(project):
            if not service.is_running():
                self.start(key[0], key[1])
        self._retime()

    def stop_all(self, project=None):
        # Reverse of the start order: what waited for something is stopped before
        # the thing it waited for, so nothing is pulled out from under a service
        # that is still using it.
        for key, service in reversed(self._ordered(project)):
            self._waiting.pop(key, None)
            if service.is_running():
                service.stop()

    def restart_all(self, project=None):
        # Each one chains its own stop-then-start through here, so a service that
        # comes back before what it waits for simply waits again.
        for key, _service in self._ordered(project):
            self.restart(key[0], key[1])
        self._retime()

    def dispose(self):
        """Stop watching, without stopping anything that is running.

        Closing the page is not an instruction to end the services on it - that
        decision belongs to :meth:`shutdown`. What must not survive is a status
        probe still on the wire, which Qt destroys mid-flight and complains about.
        """
        self._timer.stop()
        for service in self._services.values():
            service.dispose()

    def shutdown(self, timeout_ms=SHUTDOWN_WAIT_MS):
        """Stop everything that cannot outlive this window, and wait for it.

        Called from the main window's closeEvent. It waits because PySide6 binds
        no ``setChildProcessModifier``, so there is no PR_SET_PDEATHSIG to fall
        back on: if these are not down before the window is, they are orphaned.
        """
        self._timer.stop()
        doomed = self.detained()
        for service in doomed:
            service.stop()
        for service in doomed:
            service.wait_for_stop(timeout_ms)
        # Then let go of everything else that is mid-flight. A status probe still
        # on the wire when the window goes is a QProcess destroyed while running,
        # which Qt complains about on the way out and which nothing is left to
        # read the answer of.
        self.dispose()
        return len(doomed)
