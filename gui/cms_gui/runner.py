"""Driving the launcher as a child process, and turning its output into signals.

Two channels come back and they are deliberately different things:

* **stdout** carries the ``--events=-`` JSONL stream - structured, ordered,
  machine-made. It drives the Run and Artifacts pages.
* **stderr** carries human log lines. It drives the Log page.

Everything is signal-driven on the Qt event loop; nothing here ever blocks, so a
launcher that hangs leaves the GUI perfectly responsive (and stoppable).
"""

import json
import os
import re
import signal
import sys

from PySide6.QtCore import QObject, QProcess, Signal

# "13:38:26  INFO    [dev-agent] message" - the launcher's console format, with
# the session prefix the runner adds during parallel runs.
LOG_LINE = re.compile(r"^(?P<ts>\d{2}:\d{2}:\d{2})\s+(?P<level>[A-Z]+)\s+"
                      r"(?:\[(?P<session>[^\]]+)\]\s*)?(?P<text>.*)$")


def parse_log_line(line):
    """Split a console line into its parts; unparseable lines stay whole."""
    match = LOG_LINE.match(line)
    if not match:
        return {"ts": "", "level": "", "session": "", "text": line}
    return {k: (match.group(k) or "") for k in ("ts", "level", "session", "text")}


class LauncherProcess(QObject):
    """One run of ``session_launcher.py``, watched live."""

    event = Signal(dict)          # one parsed JSONL event
    log = Signal(dict)            # one parsed stderr line
    started = Signal(list)        # the argv actually used
    finished = Signal(int)        # exit code
    failed = Signal(str)          # could not start at all

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._out_buffer = ""
        self._err_buffer = ""

    # -- lifecycle ------------------------------------------------------------
    def is_running(self):
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def start(self, argv, working_dir=None):
        if self.is_running():
            return False
        proc = QProcess(self)
        # Keep the channels apart: events must never be diluted with log text.
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(self._read_stdout)
        proc.readyReadStandardError.connect(self._read_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        if working_dir:
            proc.setWorkingDirectory(working_dir)
        if os.name == "nt":
            # Own process group, so a CTRL_BREAK can be delivered on stop -
            # Windows has no SIGINT to send to another process.
            proc.setCreateProcessArgumentsModifier(_new_process_group)
        self._proc = proc
        self._out_buffer = self._err_buffer = ""
        proc.start(argv[0], list(argv[1:]))
        self.started.emit(list(argv))
        return True

    def stop(self):
        """Ask for the launcher's own graceful shutdown (it closes the windows).

        SIGINT is what the core is written around: ``keep_open_until_closed``
        turns it into the ordered ``close_all`` that flushes each login session
        to disk. Killing outright would orphan or corrupt those profiles.
        """
        if not self.is_running():
            return
        pid = int(self._proc.processId())
        if os.name == "nt":
            try:
                import ctypes
                # CTRL_BREAK_EVENT (1) - CTRL_C cannot be sent to another group.
                ctypes.windll.kernel32.GenerateConsoleCtrlEvent(1, pid)
                return
            except Exception:
                self._proc.terminate()
                return
        try:
            os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            self._proc.terminate()

    def kill(self):
        if self.is_running():
            self._proc.kill()

    # -- output ---------------------------------------------------------------
    def _read_stdout(self):
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._out_buffer += data
        # Only whole lines are events; a partial one waits for the rest.
        while "\n" in self._out_buffer:
            line, self._out_buffer = self._out_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                # Not an event: the core printed something else on stdout.
                self.log.emit({"ts": "", "level": "", "session": "", "text": line})
                continue
            if isinstance(payload, dict):
                self.event.emit(payload)

    def _read_stderr(self):
        data = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
        self._err_buffer += data
        while "\n" in self._err_buffer:
            line, self._err_buffer = self._err_buffer.split("\n", 1)
            if line.strip():
                self.log.emit(parse_log_line(line.rstrip()))

    def _flush(self):
        for buffer, emit in ((self._out_buffer, None), (self._err_buffer, self.log)):
            if buffer.strip() and emit is not None:
                emit.emit(parse_log_line(buffer.rstrip()))
        self._out_buffer = self._err_buffer = ""

    def _on_finished(self, code, _status):
        self._read_stdout()
        self._read_stderr()
        self._flush()
        self.finished.emit(int(code))

    def _on_error(self, error):
        if error == QProcess.FailedToStart:
            self.failed.emit("Could not start %s - check the interpreter and core "
                             "script in Settings." % (self._proc.program(),))


def _new_process_group(args):
    """QProcess modifier: give the child its own console group (Windows only)."""
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    args.flags |= CREATE_NEW_PROCESS_GROUP


class RunState(QObject):
    """Live model of a run, assembled from the event stream.

    The launcher reports facts one at a time (a window launched, a step ended);
    the pages want the current shape of the whole run. This is the one place
    that turns the former into the latter, so no page has to keep its own tally.
    """

    changed = Signal()
    run_dir_known = Signal(str)
    artifacts_written = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.reset()

    def reset(self):
        self.sessions = {}        # session name -> dict
        self.order = []           # session names, in launch order
        self.run_dir = ""
        self.summary = None
        self.exit_code = None
        self.started = False
        self.changed.emit()

    # -- ingestion ------------------------------------------------------------
    def _session(self, name, login=None):
        if name not in self.sessions:
            self.sessions[name] = {"name": name, "login": login or name,
                                   "state": "launching", "pid": None,
                                   "scenarios": [], "tree": None, "steps": {},
                                   "current": None, "done": 0, "total": 0,
                                   "scenario": "", "flows": []}
            self.order.append(name)
        return self.sessions[name]

    def handle(self, event):
        kind = event.get("kind", "")
        name = event.get("session")
        handler = getattr(self, "_on_" + kind.replace(".", "_"), None)
        if handler is not None:
            handler(event, name)
        self.changed.emit()

    def _on_launcher_start(self, event, _name):
        self.started = True
        for user in event.get("users", []):
            pass   # windows announce themselves individually with their profile

    def _on_window_launched(self, event, _name):
        session = self._session(event.get("session") or event.get("login", "?"),
                                event.get("login"))
        session["pid"] = event.get("pid")
        session["state"] = "launched"

    def _on_windows_ready(self, _event, _name):
        for session in self.sessions.values():
            if session["state"] == "launching":
                session["state"] = "launched"

    def _on_session_attached(self, event, name):
        session = self._session(name, event.get("login"))
        session["state"] = "attached"

    def _on_session_attach_failed(self, event, name):
        session = self._session(name, event.get("login"))
        session["state"] = "failed"

    def _on_session_start(self, event, name):
        session = self._session(name)
        session["scenarios"] = list(event.get("scenarios", []))
        session["state"] = "attached"

    def _on_flow_start(self, event, name):
        session = self._session(name)
        session["state"] = "running"
        session["scenario"] = event.get("scenario", "")
        session["tree"] = event.get("tree")
        session["total"] = int(event.get("steps") or 0)
        session["done"] = 0
        session["steps"] = {}
        session["current"] = None

    def _on_step_start(self, event, name):
        session = self._session(name)
        index = event.get("index")
        session["current"] = index
        session["steps"][index] = {"status": "running", "ms": None}

    def _on_step_end(self, event, name):
        session = self._session(name)
        index = event.get("index")
        status = event.get("status", "")
        session["steps"][index] = {"status": status, "ms": None,
                                   "message": event.get("message", "")}
        if status == "pass":
            session["done"] += 1
        session["current"] = None

    def _on_step_retry(self, event, name):
        session = self._session(name)
        step = session["steps"].setdefault(event.get("index"), {})
        step["retry"] = event.get("attempt")

    def _on_flow_end(self, event, name):
        session = self._session(name)
        status = event.get("status", "")
        session["flows"].append({"scenario": session.get("scenario", ""),
                                 "status": status,
                                 "passed": event.get("passed"),
                                 "total": event.get("total")})
        session["state"] = "passed" if status == "pass" else "failed"

    def _on_run_dir(self, event, _name):
        self.run_dir = event.get("dir", "")
        if self.run_dir:
            self.run_dir_known.emit(self.run_dir)

    def _on_artifacts_written(self, event, _name):
        self.artifacts_written.emit(event)

    def _on_run_summary(self, event, _name):
        self.summary = event

    def _on_window_exited(self, event, _name):
        for session in self.sessions.values():
            if session.get("pid") == event.get("pid"):
                if session["state"] in ("launching", "launched", "attached"):
                    session["state"] = "closed"

    # -- reading --------------------------------------------------------------
    def ordered(self):
        return [self.sessions[n] for n in self.order if n in self.sessions]

    def totals(self):
        done = sum(s["done"] for s in self.sessions.values())
        total = sum(s["total"] for s in self.sessions.values())
        return done, total
