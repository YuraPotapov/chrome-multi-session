"""The link to the core: where the launcher is, and what it says it can do.

The GUI never imports ``session_launcher`` - it spawns it. That keeps the two
environments independent (the GUI needs PySide6, the core needs playwright and
cryptography, and on Windows they are often not the same Python), and it means
the core stays the single source of truth: what environments, users, scenarios
and extensions exist all come from ``--describe``.
"""

import json
import os
import subprocess
import sys


class CoreError(Exception):
    """The launcher could not be run, or answered with something unusable."""


def _venv_python(root):
    """The interpreter inside a project's virtualenv, if it has one."""
    for parts in (("bin", "python3"), ("bin", "python"), ("Scripts", "python.exe")):
        candidate = os.path.join(root, ".venv", *parts)
        if os.path.isfile(candidate):
            return candidate
    return None


def autodetect():
    """Guess (script, interpreter) for the core, or (None, None).

    Looks next to the GUI first - ``gui/`` lives inside the core checkout, which
    is the normal layout - then at the current directory.
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # gui/
    for root in (os.path.dirname(here), os.getcwd()):
        script = os.path.join(root, "session_launcher.py")
        if os.path.isfile(script):
            return script, _venv_python(root) or sys.executable
    return None, None


class Core:
    """A configured core: an interpreter, a script, and the config it reads.

    Instances are cheap and immutable-ish; the GUI rebuilds one whenever the
    settings change rather than mutating a shared object.
    """

    def __init__(self, script=None, interpreter=None, config=None):
        auto_script, auto_python = autodetect()
        self.script = script or auto_script
        self.interpreter = interpreter or auto_python or sys.executable
        self.config = config or ""

    # -- shape of a command ---------------------------------------------------
    @property
    def root(self):
        return os.path.dirname(self.script) if self.script else ""

    @property
    def config_path(self):
        """The config the core will actually read (absolute)."""
        if self.config:
            return self.config if os.path.isabs(self.config) else os.path.join(
                self.root, self.config)
        return os.path.join(self.root, "users.json") if self.root else ""

    def is_configured(self):
        return bool(self.script and os.path.isfile(self.script)
                    and self.interpreter and os.path.isfile(self.interpreter))

    def argv(self, *args):
        """Full command line: interpreter, script, --config, then ``args``."""
        if not self.script:
            raise CoreError("No core script configured (Settings -> Core script).")
        command = [self.interpreter, self.script]
        if self.config:
            command.append("--config=" + self.config)
        command.extend(a for a in args if a)
        return command

    def display_argv(self, *args):
        """The same command, shortened for reading: basenames, no interpreter path."""
        command = self.argv(*args)
        return " ".join([os.path.basename(command[0]), os.path.basename(command[1])]
                        + command[2:])

    # -- one-shot calls -------------------------------------------------------
    def run(self, *args, timeout=60):
        """Run the launcher to completion; returns (returncode, stdout, stderr)."""
        try:
            proc = subprocess.run(self.argv(*args), capture_output=True, text=True,
                                  timeout=timeout, cwd=self.root or None,
                                  # Windows: never flash a console window.
                                  creationflags=getattr(subprocess,
                                                        "CREATE_NO_WINDOW", 0))
        except FileNotFoundError as exc:
            raise CoreError("Cannot run %s: %s" % (self.interpreter, exc))
        except subprocess.TimeoutExpired:
            raise CoreError("%s did not answer within %ds." % (
                os.path.basename(self.script or "session_launcher.py"), timeout))
        except OSError as exc:
            raise CoreError(str(exc))
        return proc.returncode, proc.stdout, proc.stderr

    def describe(self):
        """The core's inventory as a dict (see ``session_launcher.describe``)."""
        code, out, err = self.run("--describe", timeout=90)
        text = (out or "").strip()
        if not text:
            raise CoreError("--describe printed nothing (exit %d).\n%s"
                            % (code, (err or "").strip()[:400]))
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise CoreError("--describe did not return JSON (%s).\n%s"
                            % (exc, text[:400]))
        if isinstance(payload, dict) and payload.get("error") and not payload.get("users"):
            raise CoreError(payload["error"])
        return payload

    def version(self):
        _code, out, _err = self.run("--version", timeout=30)
        return (out or "").strip()

    def help_text(self):
        _code, out, _err = self.run("--help", timeout=30)
        return out or ""


class Inventory:
    """Typed, forgiving access to a ``--describe`` payload.

    The GUI must keep working against a core that is older or newer than it, so
    every accessor tolerates a missing key rather than raising deep inside a
    widget.
    """

    def __init__(self, payload=None):
        self.payload = payload or {}

    def __bool__(self):
        return bool(self.payload)

    @property
    def version(self):
        return self.payload.get("version", "")

    @property
    def config_path(self):
        return self.payload.get("config_path", "")

    @property
    def envs(self):
        return list(self.payload.get("envs", []))

    @property
    def users(self):
        return list(self.payload.get("users", []))

    @property
    def scenarios(self):
        return list(self.payload.get("scenarios", []))

    @property
    def extensions(self):
        return list(self.payload.get("extensions", []))

    @property
    def tags(self):
        return list(self.payload.get("tags", []))

    @property
    def warnings(self):
        return list(self.payload.get("warnings", []))

    def env_aliases(self):
        return [e.get("alias", "") for e in self.envs]

    def env_value(self, alias):
        """The users.json ``env`` string behind an ``--env`` alias.

        ``--env`` matches on the alias, but the user rows carry the raw value, so
        anything selecting users by environment has to cross this bridge.
        """
        for env in self.envs:
            if env.get("alias") == alias:
                return env.get("value", "")
        return ""

    def logins(self, env_value=None):
        """Logins, optionally only those belonging to one environment value."""
        return [u.get("login", "") for u in self.users
                if env_value in (None, u.get("env"))]

    def choices(self, key, fallback):
        """A vocabulary the core advertises (log levels, overlay components...)."""
        value = self.payload.get(key)
        return list(value) if isinstance(value, list) and value else list(fallback)

    def dirs(self):
        return {
            "flows": self.payload.get("flows_dir", ""),
            "reports": self.payload.get("reports_dir", ""),
            "sessions": self.payload.get("sessions_dir", ""),
        }

    def summary(self):
        """One line for the toolbar: what this inventory contains."""
        if not self.payload:
            return "no core inventory"
        return "--describe · %d envs · %d users · %d scenarios" % (
            len(self.envs), len(self.users), len(self.scenarios))
