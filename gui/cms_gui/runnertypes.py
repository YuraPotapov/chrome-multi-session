"""The kinds of service the Services & Logs page can start, as one table.

A **runner** is something the page can start, stop and report the state of: a
local server, a container, an `npm run dev`. What differs between them is small and
regular - the command line to start, the command line to stop, how to ask whether
it is up, and which fields the form has to collect - so each kind is one entry in
:data:`TYPES` rather than a plugin discovered at import time. That is the shape
the rest of the application already uses for things that grow: ``commands.FLAGS``,
``icons.DRAWINGS``, ``serverlog.FORMATS``. Adding a kind is adding a class and a
name to the tuple at the bottom; nothing scans, nothing registers itself, and the
set is greppable.

**Forms are generated, not written.** Each type declares :attr:`~RunnerType.fields`
and the dialog builds itself from them. A hand-written editor per type would be
four near-identical QWidgets that drift apart, and the interesting part of a type
is its command lines, not its layout.

The one distinction that is *not* cosmetic is :attr:`~RunnerType.mode`:

* ``SUPERVISED`` - we spawn the process and it is ours. Its state is the process's
  state, its output comes back on a pipe, and stopping it means signalling it.
* ``MANAGED`` - we ask something else to run it. ``docker start`` returns the
  moment the daemon has the container; the container's life has nothing to do
  with the life of that command. So state cannot come from the process we ran and
  has to be asked for separately, which is what :meth:`~RunnerType.probe_command`
  is for.

Nothing here spawns anything or touches Qt - it builds argument lists and reads
their output. ``services.py`` runs them.
"""

import os
import shlex
from collections import namedtuple

#: Who owns the running thing. See the module docstring.
SUPERVISED = "supervised"
MANAGED = "managed"

#: The status vocabulary, shared with ``services.py`` and the page.
STOPPED = "stopped"
#: Asked to start, but something it depends on is not up yet. Its own place in
#: the vocabulary because "nothing is happening yet, and that is correct" is a
#: different thing to report than "starting".
WAITING = "waiting"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
FAILED = "failed"

#: What `python` is called on a machine. Not ``sys.executable``: that is the GUI's
#: own interpreter inside its PySide6 venv, which is the one interpreter a
#: project's service almost certainly must not run under.
DEFAULT_PYTHON = "python" if os.name == "nt" else "python3"

#: One field of a generated form.
#:
#: ``kind`` is what the dialog builds:
#:   ``text``  a line edit
#:   ``file``  a line edit with a Browse… that opens a file chooser
#:   ``dir``   a line edit with a Browse… that opens a directory chooser
#:   ``args``  a line edit split the way a shell would split it
#:   ``env``   a small name/value grid
#:   ``check`` a checkbox
Field = namedtuple("Field", "key label kind hint required")


def field(key, label, kind="text", hint="", required=False):
    return Field(key, label, kind, hint, required)


def split_args(text):
    """Split a command line the way a shell would - without running one.

    Raises ``ValueError`` on an unbalanced quote, which is a typo the form should
    report rather than a crash halfway through starting a service.
    """
    if not text or not text.strip():
        return []
    if os.name == "nt":
        # posix=False, because the posix lexer treats a backslash as an escape
        # and would eat every separator in C:\path\to\thing. It leaves the quotes
        # on a quoted token, though, and the child must never see those.
        return [token[1:-1] if len(token) > 1 and token[0] == token[-1] == '"'
                else token
                for token in shlex.split(text, posix=False)]
    return shlex.split(text)


#: Where a project keeps its virtualenv, and what the interpreter is called
#: inside one. Both spellings of the directory, because both are common, and the
#: Windows layout as well as the POSIX one.
VENV_DIRS = (".venv", "venv", "env")
VENV_PYTHONS = (("bin", "python3"), ("bin", "python"), ("Scripts", "python.exe"))


def venv_python(project_dir):
    """The interpreter inside a project's own virtualenv, if it has one.

    Worth detecting rather than asking for: it is the single most likely answer
    to "which python", and it lives in a dotted directory that a file chooser
    will not list - so the alternative is a Browse button that cannot reach the
    one file it is most often opened for.
    """
    root = os.path.expanduser((project_dir or "").strip())
    if not root or not os.path.isdir(root):
        return ""
    for name in VENV_DIRS:
        for parts in VENV_PYTHONS:
            candidate = os.path.join(root, name, *parts)
            if os.path.isfile(candidate):
                return candidate
    return ""


def resolve_dir(settings, project_dir):
    """The working directory a runner actually starts in.

    A runner's own directory wins; a relative one is read against the project's,
    which is what makes ``addons`` mean something in a project rooted somewhere.
    """
    own = (settings.get("dir") or "").strip()
    base = (project_dir or "").strip()
    if not own:
        return os.path.expanduser(base) if base else ""
    own = os.path.expanduser(own)
    if os.path.isabs(own) or not base:
        return own
    return os.path.normpath(os.path.join(os.path.expanduser(base), own))


#: Every supervised service gets this, whatever kind it is: how long it may take
#: to stop before it is killed. It is not a property of the *type* - a shell
#: command and a python script are both as slow to shut down as what they run -
#: so it is appended to each form rather than written into every type's fields.
STOP_GRACE_FIELD = field(
    "stop_grace", "Stop timeout", "text",
    "Seconds to let it shut down before it is killed. Blank uses 8. Raise it "
    "for anything that must finish what it is doing - a migration, a module "
    "update - and use 0 to never kill it, which leaves it Stopping until it "
    "goes on its own.")


def stop_grace_problems(settings):
    """Everything wrong with a stop timeout. Blank is fine - it means the default.

    Checked rather than quietly ignored: a value that does not parse would fall
    back to eight seconds, and the one service anybody sets this on is the one
    where eight seconds is the wrong answer.
    """
    raw = str((settings or {}).get("stop_grace", "") or "").strip()
    if not raw:
        return []
    try:
        seconds = float(raw)
    except ValueError:
        return ["Stop timeout: %r is not a number of seconds." % raw]
    if seconds < 0:
        return ["Stop timeout cannot be negative. Use 0 to never kill it."]
    return []


class RunnerType(object):
    """One kind of service: its form, its command lines, and how to ask its state."""

    id = ""
    label = ""
    #: A name in ``icons.DRAWINGS``.
    icon = "run"
    mode = SUPERVISED
    #: May the user choose whether this survives the GUI closing?
    detach_choice = True
    #: What a new runner of this kind gets, and what it is fixed at when there is
    #: no choice.
    detach_default = False
    #: Why the choice is not offered. Shown beside the disabled checkbox.
    detach_note = ""
    fields = ()

    # -- the form -------------------------------------------------------------
    def form_fields(self):
        """What the dialog builds and what ``problems`` checks.

        A managed service is stopped by asking its daemon - ``docker stop`` has
        a timeout of its own - so the grace below is not its to answer for.
        """
        if self.mode == MANAGED:
            return tuple(self.fields)
        return tuple(self.fields) + (STOP_GRACE_FIELD,)

    def default_settings(self):
        return {f.key: {} if f.kind == "env" else
                       (False if f.kind == "check" else "")
                for f in self.form_fields()}

    def problems(self, settings):
        """Everything wrong with these settings, as messages. Never raises.

        The generic half - required fields and lexable command lines - is here so
        a type only writes down what is peculiar to it.
        """
        problems = []
        for spec in self.form_fields():
            value = settings.get(spec.key)
            if spec.required and not str(value or "").strip():
                problems.append("%s is required." % spec.label)
            if spec.kind == "args" and value:
                try:
                    split_args(value)
                except ValueError as exc:
                    problems.append("%s: %s." % (spec.label, exc))
        problems.extend(stop_grace_problems(settings))
        return problems

    # -- the command lines ----------------------------------------------------
    def command(self, settings, cwd):
        """The argv that starts this. ``cwd`` is already resolved."""
        raise NotImplementedError

    def stop_command(self, settings):
        """The argv that stops it, or None to signal the process we started."""
        return None

    def probe_command(self, settings):
        """The argv that answers "is it up?", or None when our own process is."""
        return None

    def probe_reads(self, stdout, code):
        """Turn a probe's output into :data:`RUNNING` or :data:`STOPPED`."""
        return RUNNING if code == 0 else STOPPED

    # -- rendering ------------------------------------------------------------
    def summary(self, settings):
        """One line for the page's Config column: what this runner actually runs."""
        return ""


class PythonRunner(RunnerType):
    id = "python"
    label = "Python Script"
    icon = "command"
    mode = SUPERVISED
    detach_default = False
    fields = (
        field("interpreter", "Interpreter", "file",
              "Blank finds the project's own .venv, and falls back to %s from "
              "PATH. Name one here only to override that." % DEFAULT_PYTHON),
        field("script", "Script", "file", "e.g. manage.py. Relative to the working "
              "directory below.", required=True),
        field("args", "Arguments", "args", "Split the way a shell would split "
              "them, e.g. -c app.conf --debug"),
        field("dir", "Working directory", "dir",
              "Blank uses the project's own directory."),
        field("env", "Environment", "env",
              "Added to what this application was started with."),
    )

    def interpreter(self, settings, cwd=""):
        """What this actually runs under: what was named, the project's venv, or
        whatever ``python3`` resolves to."""
        return ((settings.get("interpreter") or "").strip()
                or venv_python(cwd) or DEFAULT_PYTHON)

    def command(self, settings, cwd):
        return ([os.path.expanduser(self.interpreter(settings, cwd)),
                 (settings.get("script") or "").strip()]
                + split_args(settings.get("args") or ""))

    def summary(self, settings):
        interpreter = (settings.get("interpreter") or "").strip() or DEFAULT_PYTHON
        parts = [os.path.basename(interpreter), (settings.get("script") or "").strip()]
        args = (settings.get("args") or "").strip()
        if args:
            parts.append(args)
        return " ".join(p for p in parts if p)


class ShellRunner(RunnerType):
    id = "shell"
    label = "Shell Command"
    icon = "command"
    mode = SUPERVISED
    detach_default = False
    fields = (
        field("command", "Command", "args",
              "The whole command line, e.g. npm run dev. It is split here, not "
              "handed to a shell - so no pipes, no redirection, no && .",
              required=True),
        field("dir", "Working directory", "dir",
              "Blank uses the project's own directory."),
        field("env", "Environment", "env",
              "Added to what this application was started with."),
    )

    def command(self, settings, cwd):
        return split_args(settings.get("command") or "")

    def problems(self, settings):
        problems = RunnerType.problems(self, settings)
        # A command that lexes to nothing is not a command, and QProcess would
        # report the failure only once the user pressed Start.
        text = (settings.get("command") or "").strip()
        if text and not problems:
            try:
                if not split_args(text):
                    problems.append("Command is empty.")
            except ValueError:
                pass          # already reported by the generic pass
        return problems

    def summary(self, settings):
        return (settings.get("command") or "").strip()


class DockerRunner(RunnerType):
    id = "docker"
    label = "Docker Container"
    icon = "artifacts"
    mode = MANAGED
    detach_choice = False
    detach_default = True
    detach_note = ("A container belongs to the Docker daemon, not to this "
                   "application - closing the window never stops one.")
    fields = (
        field("container", "Container", "text",
              "Name or id, as `docker ps` shows it. The container has to exist "
              "already; this starts and stops it, it does not create it.",
              required=True),
    )

    def _name(self, settings):
        return (settings.get("container") or "").strip()

    def command(self, settings, cwd):
        return ["docker", "start", self._name(settings)]

    def stop_command(self, settings):
        return ["docker", "stop", self._name(settings)]

    def probe_command(self, settings):
        return ["docker", "inspect", "-f", "{{.State.Running}}",
                self._name(settings)]

    def probe_reads(self, stdout, code):
        # A container that does not exist exits non-zero, which is "stopped" as
        # far as the page is concerned - the row says so, and Start reports the
        # real error from the daemon.
        if code != 0:
            return STOPPED
        return RUNNING if stdout.strip().lower() == "true" else STOPPED

    def summary(self, settings):
        return self._name(settings)


class ComposeRunner(RunnerType):
    id = "compose"
    label = "Docker Compose"
    icon = "artifacts"
    mode = MANAGED
    detach_choice = False
    detach_default = True
    detach_note = DockerRunner.detach_note
    fields = (
        field("file", "Compose file", "file",
              "Path to docker-compose.yml.", required=True),
        field("services", "Services", "args",
              "Blank starts everything in the file. Otherwise name them, "
              "separated by spaces."),
    )

    def _base(self, settings):
        # The v2 spelling. A machine with only the old standalone docker-compose
        # gets that command's own error, which names the problem exactly - better
        # than a silent fallback that makes two spellings mean two behaviours.
        return ["docker", "compose", "-f",
                os.path.expanduser((settings.get("file") or "").strip())]

    def _services(self, settings):
        return split_args(settings.get("services") or "")

    def command(self, settings, cwd):
        return self._base(settings) + ["up", "-d"] + self._services(settings)

    def stop_command(self, settings):
        # down, not stop: it is the counterpart of `up`, and leaving the network
        # and the containers behind is how a project ends up half up next time.
        return self._base(settings) + ["down"]

    def probe_command(self, settings):
        return (self._base(settings) + ["ps", "--status=running", "-q"]
                + self._services(settings))

    def probe_reads(self, stdout, code):
        if code != 0:
            return STOPPED
        # One id per running container. None means nothing in this file is up.
        return RUNNING if stdout.strip() else STOPPED

    def summary(self, settings):
        path = (settings.get("file") or "").strip()
        services = " ".join(self._services_safe(settings))
        name = os.path.basename(path) or path
        return "%s  %s" % (name, services) if services else name

    def _services_safe(self, settings):
        try:
            return self._services(settings)
        except ValueError:
            return []


#: Every kind of service, in the order the "+ Add Configuration" menu offers them.
TYPES = (PythonRunner(), ShellRunner(), DockerRunner(), ComposeRunner())

BY_ID = {runner.id: runner for runner in TYPES}


def get(type_id):
    """The type with this id, or None. Never raises - a file may name anything."""
    return BY_ID.get(type_id)


def label_of(type_id):
    """What to show in the page's Type column for a possibly-unknown id."""
    runner = BY_ID.get(type_id)
    return runner.label if runner else "%s (unknown)" % (type_id or "?")
