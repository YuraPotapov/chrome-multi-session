"""Backend log streams, filtered to the window that was open when they were written.

The problem this solves: ten windows are open as ten different roles, one of them
misbehaves, and the server's log is a single stream with everyone's requests mixed
together. This module tails that stream and hands each session only the lines that
belong to it.

**Connections and logs are separate on purpose.** One environment has several logs -
the app, nginx, the system journal - but they all live on one machine. So a
*connection* says **where to run** (locally, or over ssh) and a *log* says **what to
run** (tail a file, follow a container, follow a unit, read an HTTP stream). Four
readers times two connections come out of two small axes instead of eight classes,
and a stand with three logs opens one ssh connection rather than three.

**Correlation is by time, and that is a real limitation.** A session sees the lines
written after its window opened. Nothing here inspects the request, so when several
windows are open against the same environment they all see the same tail - time alone
cannot say which of them made the call. :class:`SinceOpened` is therefore a strategy
object rather than an ``if``: a precise key (a header the profile's extension adds, a
session id the backend logs) drops in as another class without touching the plumbing.

Nothing here may take a launch down. An unreachable host, a log file that is not
there, a format that will not parse - each costs that one log, is reported once, and
leaves the rest of the run alone.
"""

import collections
import json
import logging
import math
import os
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("flowengine.serverlog")


class ServerLogError(Exception):
    """A logsources.json that cannot be used as written."""


CONNECTION_TYPES = ("local", "ssh")
LOG_TYPES = ("file", "docker", "journal", "http")

# How many parsed lines each log keeps for the report artifact. A run that outlives
# the buffer loses its oldest lines, not its newest - the ones next to a failure.
BUFFER_LINES = 5000

# GUI batching: how long lines accumulate before they are announced, and the ceiling
# on one batch. The ceiling caps the *event stream* only; the buffer above keeps
# every line, so a burst is trimmed on screen and still lands whole in the artifact.
BATCH_SECONDS = 0.25
BATCH_MAX_LINES = 200

# How far a line's own timestamp may sit from the moment the reader saw it before
# the timestamp is treated as misread rather than the clock as wrong. Generous:
# this is meant to catch a whole-hour timezone mistake, not ordinary buffering.
MAX_DRIFT_SECONDS = 120

# Reading a log to look at it, rather than following it. The byte budget is the
# real limit: a production log runs to hundreds of megabytes, and "open the whole
# thing" has to mean "as much of the end of it as is sane to move and to render".
READ_TAIL_LINES = 500
READ_MAX_BYTES = 4_000_000

# A reader that dies (network blip, container restart) comes back this many times,
# waiting a little longer each go, before the log is given up as unavailable.
_RETRIES = 3
_RETRY_BACKOFF = (1.0, 3.0, 8.0)


# -- line formats -------------------------------------------------------------
# Presets are named after the SHAPE of the line, not after an application. Every
# backend that writes "2026-08-19 10:53:09,123" gets the same treatment whether it
# is Django, Flask, Celery, Gunicorn or Odoo, and naming the preset after one of
# them would be a claim this module has no business making. Application names are
# offered as aliases below, because that is what somebody actually looks for.
#
# Anything these do not cover is served by giving "timestamp" and "level" on the
# log itself - the presets are a convenience, not the boundary of what works.

# One case-insensitive alternation covering the level vocabularies in common use.
# Deliberately generous: the level only tints a line in the panel, so matching the
# word "error" inside a message is a fair trade for working on an unknown backend.
_LEVEL_WORD = (r"(?i)\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRIT"
               r"|CRITICAL|FATAL|ALERT|EMERG|PANIC)\b")

FORMATS = {
    # 2026-08-19T10:53:09.123Z, 2026-08-19 10:53:09,123, 2026-08-19T10:53:09+03:00.
    # The widest net there is: ISO-8601 in all its spellings, which also happens to
    # be Python logging's default %(asctime)s - so this one preset reads Django,
    # Flask, FastAPI/uvicorn, Celery, Gunicorn, Odoo, Node/pino, Rails and
    # `docker logs -t` alike. Parsed by _parse_iso.
    "iso": {
        "timestamp": {"regex": r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                               r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
                      "format": "iso", "tz": "local"},
        "level": {"regex": _LEVEL_WORD},
    },
    # 2026/08/19 10:53:09 - nginx's error log, and Go's standard log package.
    "slash": {
        "timestamp": {"regex": r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})",
                      "format": "%Y/%m/%d %H:%M:%S", "tz": "local"},
        # nginx brackets its level, which is far more precise than a loose word
        # match on a line that is mostly a URL.
        "level": {"regex": r"\[(debug|info|notice|warn|error|crit|alert|emerg)\]"},
    },
    # [19/Aug/2026:10:53:09 +0300] - Common Log Format: Apache and nginx access
    # logs, and anything imitating them.
    "clf": {
        "timestamp": {"regex": r"\[(\d{2}/[A-Za-z]{3}/\d{4}[: ]\d{2}:\d{2}:\d{2}"
                               r"(?: [+-]\d{4})?)\]",
                      "format": "clf", "tz": "local"},
        "level": {"regex": _LEVEL_WORD},
    },
    # Aug 19 10:53:09 host program[42]: message - rsyslog, and what `journalctl -f`
    # prints. No year in the line; see _fill_missing_year.
    "syslog": {
        "timestamp": {"regex": r"^([A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2})",
                      "format": "%b %d %H:%M:%S", "tz": "local"},
        "level": {"regex": _LEVEL_WORD},
    },
    # No timestamp in the line at all - plenty of Node and container logs. Every
    # line is stamped with the moment it arrived, which for a live tail is honest:
    # the reader saw it now, so it belongs to whatever window is open now.
    "none": {
        "timestamp": {"regex": r"(?!x)x", "format": "iso", "tz": "local"},
        "level": {"regex": _LEVEL_WORD},
    },
}

# What people actually call their backend, pointing at the shape it writes. Kept
# separate from FORMATS so the presets stay honest about what they match, while
# "format": "django" still works and reads better in a config file.
FORMAT_ALIASES = {
    "python": "iso", "odoo": "iso", "django": "iso", "flask": "iso",
    "fastapi": "iso", "uvicorn": "iso", "celery": "iso", "gunicorn": "iso",
    "rails": "iso", "node": "iso", "pino": "iso", "winston": "iso",
    "dotnet": "iso", "spring": "iso", "docker": "iso",
    "nginx": "slash", "go": "slash",
    "apache": "clf", "access": "clf",
    "journal": "syslog", "systemd": "syslog", "rsyslog": "syslog",
    "raw": "none", "plain": "none",
}

DEFAULT_FORMAT = "iso"


def known_formats():
    """Every value ``format`` accepts: the shapes, then the application aliases."""
    return sorted(FORMATS) + sorted(FORMAT_ALIASES)


def resolve_format(name):
    """The preset for a format name, following aliases. None if unknown."""
    name = (name or DEFAULT_FORMAT).strip().lower()
    return FORMATS.get(FORMAT_ALIASES.get(name, name))

# Severity, least to most. Deliberately NOT engine.overlay's three buckets: the
# HUD has one small widget and folds everything alarming into ERROR, while a log
# viewer is the one place where "the process is going down" has to look different
# from "that request failed", and where DEBUG chatter has to be able to recede.
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")

# Every level word in common use across backends, onto the five above. Whatever a
# log calls it, this is what the panel colours and what the filter compares.
_LEVEL_ALIASES = {
    "trace": "DEBUG", "debug": "DEBUG", "verbose": "DEBUG", "fine": "DEBUG",
    "info": "INFO", "notice": "INFO", "information": "INFO",
    "warn": "WARN", "warning": "WARN",
    "err": "ERROR", "error": "ERROR", "severe": "ERROR",
    "crit": "CRITICAL", "critical": "CRITICAL", "fatal": "CRITICAL",
    "alert": "CRITICAL", "emerg": "CRITICAL", "emergency": "CRITICAL",
    "panic": "CRITICAL",
}


def normalize_level(name):
    """One of :data:`LEVELS` for whatever word a backend used. Unknown -> INFO."""
    return _LEVEL_ALIASES.get((name or "").strip().lower(), "INFO")


# -- config model -------------------------------------------------------------
class Connection(object):
    """Where to run a reader: this machine, or one ssh hop away."""

    def __init__(self, name, type="local", host="", user="", identity="",
                 port=None, options=(), extra=None):
        self.name = name
        self.type = type
        self.host = host
        self.user = user
        self.identity = identity
        self.port = port
        self.options = tuple(options)
        self.extra = dict(extra or {})

    def __repr__(self):
        return "Connection(%r, %r)" % (self.name, self.type)

    def describe(self):
        if self.type == "local":
            return "local"
        return "ssh %s" % self.target()

    def target(self):
        return "%s@%s" % (self.user, self.host) if self.user else self.host

    def wrap(self, argv):
        """The command line that runs ``argv`` on the far side of this connection."""
        if self.type == "local":
            return list(argv)
        # BatchMode: never sit on a password prompt inside a background thread.
        # The host key is deliberately NOT auto-accepted - an unknown host fails
        # with a message telling the user to ssh in once, which is a better trade
        # than silently trusting whatever answers on that address.
        ssh = ["ssh", "-o", "BatchMode=yes", "-T"]
        for option in self.options:
            ssh += ["-o", option]
        if self.identity:
            ssh += ["-i", os.path.expanduser(self.identity)]
        if self.port:
            ssh += ["-p", str(self.port)]
        ssh.append(self.target())
        ssh.append(" ".join(shlex.quote(part) for part in argv))
        return ssh


class LogSource(object):
    """One log stream: what to read, on which connection, for which environments."""

    def __init__(self, name, connection, envs=(), type="file", path="", container="",
                 unit="", url="", headers=None, format=DEFAULT_FORMAT,
                 timestamp=None, level=None, tz="", default=False, extra=None):
        self.name = name
        self.connection = connection          # resolved to a Connection by load_config
        self.envs = tuple(envs)
        self.type = type
        self.path = path
        self.container = container
        self.unit = unit
        self.url = url
        self.headers = dict(headers or {})
        self.format = format
        self.timestamp = dict(timestamp) if timestamp else None
        self.level = dict(level) if level else None
        # Overrides whatever the preset says, so a backend that logs UTC needs one
        # word rather than a whole hand-written timestamp block copied off a preset.
        self.tz = tz or ""
        self.default = bool(default)
        self.extra = dict(extra or {})

    def __repr__(self):
        return "LogSource(%r, %r)" % (self.name, self.type)

    def serves(self, env):
        return env in self.envs

    def describe(self):
        """A one-line summary for --server-log=list and --describe."""
        what = {"file": self.path, "docker": self.container,
                "journal": self.unit or "*", "http": self.url}[self.type]
        return "%s (%s) %s" % (self.type, self.connection.describe(), what)

    def command(self, follow=True, tail=None):
        """The reader command, before the connection wraps it. None for http.

        ``follow`` is what a run needs: start at the end and keep going, because
        only what happens from now on can belong to a window opening now. Without
        it this reads what is already there and stops, which is what somebody
        looking at the log itself wants - ``tail`` lines of it, or all of it when
        ``tail`` is None.
        """
        if self.type == "file":
            if follow:
                return ["tail", "-n", "0", "-F", self.path]
            return (["tail", "-n", str(tail), self.path] if tail
                    else ["cat", self.path])
        if self.type == "docker":
            argv = ["docker", "logs"]
            if follow:
                return argv + ["-n", "0", "-f", self.container]
            return argv + (["-n", str(tail)] if tail else []) + [self.container]
        if self.type == "journal":
            argv = ["journalctl", "--no-pager"]
            argv += ["-f", "-n", "0"] if follow else ["-n", str(tail or "all")]
            if self.unit and self.unit != "*":
                argv += ["-u", self.unit]
            return argv
        return None


class Config(object):
    """A parsed logsources.json."""

    def __init__(self, connections=(), logs=()):
        self.connections = list(connections)
        self.logs = list(logs)

    def envs(self):
        """Every environment any log is bound to."""
        seen = []
        for source in self.logs:
            for env in source.envs:
                if env not in seen:
                    seen.append(env)
        return seen

    def for_env(self, env):
        return [source for source in self.logs if source.serves(env)]


_CONNECTION_KEYS = ("name", "type", "host", "user", "identity", "port", "options")
_LOG_KEYS = ("name", "connection", "envs", "env", "type", "path", "container",
             "unit", "url", "headers", "format", "timestamp", "level", "tz",
             "default")


def _require(entry, key, where):
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ServerLogError("%s: %r is required and must be a non-empty string" % (where, key))
    return value.strip()


def _one_of(value, allowed, key, where):
    if value not in allowed:
        raise ServerLogError("%s: unknown %s %r. Known: %s"
                             % (where, key, value, ", ".join(allowed)))
    return value


def _parse_connection(entry, index):
    where = "connections[%d]" % index
    if not isinstance(entry, dict):
        raise ServerLogError("%s must be a JSON object, got %r" % (where, entry))
    name = _require(entry, "name", where)
    kind = _one_of(entry.get("type", "local"), CONNECTION_TYPES, "type", where)
    if kind == "ssh" and not entry.get("host"):
        raise ServerLogError("%s: an ssh connection needs a %r" % (where, "host"))
    options = entry.get("options", ())
    if isinstance(options, str):
        options = [options]
    if not isinstance(options, (list, tuple)):
        raise ServerLogError("%s: %r must be a list of ssh -o settings" % (where, "options"))
    return Connection(
        name=name, type=kind, host=entry.get("host", ""), user=entry.get("user", ""),
        identity=entry.get("identity", ""), port=entry.get("port"), options=options,
        extra={k: v for k, v in entry.items() if k not in _CONNECTION_KEYS})


def _parse_log(entry, index, connections):
    where = "logs[%d]" % index
    if not isinstance(entry, dict):
        raise ServerLogError("%s must be a JSON object, got %r" % (where, entry))
    name = _require(entry, "name", where)
    where = "logs[%d] (%s)" % (index, name)
    connection_name = _require(entry, "connection", where)
    if connection_name not in connections:
        raise ServerLogError("%s: no connection named %r. Defined: %s"
                             % (where, connection_name,
                                ", ".join(sorted(connections)) or "(none)"))
    kind = _one_of(entry.get("type", "file"), LOG_TYPES, "type", where)
    # "env" is accepted as the singular spelling of "envs" - a log bound to one
    # environment is the common case and the plural reads like a typo there.
    envs = entry.get("envs", entry.get("env", ()))
    if isinstance(envs, str):
        envs = [envs]
    if not isinstance(envs, (list, tuple)) or not envs:
        raise ServerLogError("%s: %r must name at least one environment "
                             "(the same string users.json uses)" % (where, "envs"))
    for env in envs:
        if not isinstance(env, str) or not env.strip():
            raise ServerLogError("%s: %r entries must be environment strings, got %r"
                                 % (where, "envs", env))
    required = {"file": "path", "docker": "container", "http": "url"}.get(kind)
    if required:
        _require(entry, required, where)
    fmt = entry.get("format", DEFAULT_FORMAT)
    if resolve_format(fmt) is None:
        raise ServerLogError("%s: unknown format %r. Known: %s. Any other backend "
                             "works by giving %r and %r on the log itself."
                             % (where, fmt, ", ".join(known_formats()),
                                "timestamp", "level"))
    headers = entry.get("headers", {})
    if not isinstance(headers, dict):
        raise ServerLogError("%s: %r must be a JSON object" % (where, "headers"))
    if entry.get("tz"):
        try:
            _tzinfo(entry["tz"])
        except ServerLogError as exc:
            raise ServerLogError("%s: %s" % (where, exc))
    return LogSource(
        name=name, connection=connections[connection_name],
        envs=[env.strip() for env in envs], type=kind,
        path=entry.get("path", ""), container=entry.get("container", ""),
        unit=entry.get("unit", ""), url=entry.get("url", ""), headers=headers,
        format=fmt, timestamp=entry.get("timestamp"), level=entry.get("level"),
        tz=entry.get("tz", ""), default=entry.get("default", False),
        extra={k: v for k, v in entry.items() if k not in _LOG_KEYS})


def parse_config(data, where="logsources.json"):
    """Build a :class:`Config` from already-decoded JSON."""
    if not isinstance(data, dict):
        raise ServerLogError("%s must be a JSON object with %r and %r"
                             % (where, "connections", "logs"))
    raw_connections = data.get("connections", [])
    raw_logs = data.get("logs", [])
    if not isinstance(raw_connections, list) or not isinstance(raw_logs, list):
        raise ServerLogError("%s: %r and %r must both be JSON arrays"
                             % (where, "connections", "logs"))
    connections = {}
    for index, entry in enumerate(raw_connections):
        connection = _parse_connection(entry, index)
        if connection.name in connections:
            raise ServerLogError("connections[%d]: duplicate name %r"
                                 % (index, connection.name))
        connections[connection.name] = connection
    logs = []
    # A name has to be unique per environment, not globally: "odoo" exists on every
    # stand and that is the point, but --server-log=odoo must mean one log per run.
    seen = set()
    for index, entry in enumerate(raw_logs):
        source = _parse_log(entry, index, connections)
        for env in source.envs:
            if (env, source.name) in seen:
                raise ServerLogError("logs[%d]: %r is already defined for environment "
                                     "%r; names must be unique per environment"
                                     % (index, source.name, env))
            seen.add((env, source.name))
        logs.append(source)
    return Config(connections.values(), logs)


def load_config(path):
    """Read and validate logsources.json. Missing file -> an empty config."""
    if not os.path.isfile(path):
        return Config()
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ServerLogError("Could not read %s: %s" % (path, exc))
    return parse_config(data, where=path)


# -- selection ----------------------------------------------------------------
ALL = "all"
NONE = "none"
LIST = "list"


def resolve(config, envs, requested=None, strict=True):
    """The logs to stream for one environment, or for every one a run touches.

    ``envs`` is a single environment string or an iterable of them - a launch
    without ``--env`` opens windows on all of them at once, and each gets its own
    logs. ``requested`` is None (the ones marked ``"default": true``), :data:`ALL`,
    :data:`NONE`, or a list of names.

    A name only has to match *somewhere* in the run. Asking for ``nginx`` across a
    local stand and a remote one is a reasonable thing to type, and failing because
    the local one has no nginx would make the flag useless the moment ``--env`` is
    left off. A name that matches nowhere is still wrong - that is a typo.

    ``strict`` decides what "wrong" costs. True raises, which is what a listing or
    a test wants. False warns and returns whatever *did* match, which is what a
    launch wants: asking for two logs and misspelling one must not cost the other,
    and must certainly not cost the windows.
    """
    if isinstance(envs, str):
        envs = [envs]
    envs = list(envs)
    if requested == NONE:
        return []
    chosen, matched = [], set()
    for env in envs:
        available = config.for_env(env)
        if requested == ALL:
            picked = available
        elif requested is None:
            picked = [source for source in available if source.default]
        else:
            by_name = {source.name: source for source in available}
            picked = [by_name[name] for name in requested if name in by_name]
            matched.update(name for name in requested if name in by_name)
        for source in picked:
            if source not in chosen:
                chosen.append(source)
    if requested is not None and requested != ALL:
        missing = [name for name in requested if name not in matched]
        if missing:
            here = sorted({s.name for env in envs for s in config.for_env(env)})
            elsewhere = sorted({s.name for s in config.logs} - set(here))
            hint = " (configured, but not for %s)" % (
                " / ".join(repr(e) for e in envs)) if any(
                    name in elsewhere for name in missing) else ""
            message = ("no log named %s%s. Available for this run: %s"
                       % (", ".join(repr(name) for name in missing), hint,
                          ", ".join(here) or "(none)"))
            if strict:
                raise ServerLogError(message)
            log.warning("--server-log: %s", message)
    return chosen


# -- timestamps ---------------------------------------------------------------
def _tzinfo(spec):
    """A tzinfo for a "tz" setting, or None meaning "this machine's local time"."""
    if not spec or spec == "local":
        return None
    if spec == "utc":
        return timezone.utc
    match = re.match(r"^([+-])(\d{2}):?(\d{2})$", spec)
    if not match:
        raise ServerLogError("unknown tz %r; use \"local\", \"utc\" or \"+HH:MM\"" % spec)
    sign = -1 if match.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=int(match.group(2)),
                                     minutes=int(match.group(3))))


def _parse_iso(text):
    """datetime.fromisoformat, minus the spellings Python 3.9 refuses.

    3.9 rejects a trailing "Z" and accepts a fraction only at exactly 3 or 6 digits,
    which real logs do not respect. Normalising here keeps the floor at 3.9.
    """
    text = text.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    match = re.match(r"^(.*?[T]\d{2}:\d{2}:\d{2})(?:[.,](\d+))?(.*)$", text)
    if not match:
        raise ValueError("not an ISO timestamp: %r" % text)
    head, fraction, tail = match.group(1), match.group(2), match.group(3)
    if fraction:
        head += ".%s" % fraction[:6].ljust(6, "0")
    # "+0300" -> "+03:00"; 3.9 wants the colon.
    tail = re.sub(r"^([+-]\d{2})(\d{2})$", r"\1:\2", tail)
    return datetime.fromisoformat(head + tail)


def _parse_clf(text):
    """Common Log Format: ``19/Aug/2026:10:53:09 +0300``.

    Django's development server writes the same date with a space instead of the
    colon and no offset at all, so both are accepted rather than shipping two
    presets that differ by one character.
    """
    text = text.strip().strip("[]")
    text = re.sub(r"^(\d{2}/[A-Za-z]{3}/\d{4})[ :]", r"\1:", text, count=1)
    for pattern in ("%d/%b/%Y:%H:%M:%S %z", "%d/%b/%Y:%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise ValueError("not a Common Log Format timestamp: %r" % text)


def _fill_missing_year(moment, now):
    """Give a syslog-shaped timestamp the year it does not carry.

    strptime defaults to 1900. The current year is right all but a few days a year;
    around New Year a December line read in January would land in the future, so a
    timestamp that ends up more than a day ahead is pulled back a year.
    """
    if moment.year != 1900:
        return moment
    candidate = moment.replace(year=now.year)
    if candidate - now > timedelta(days=1):
        candidate = candidate.replace(year=now.year - 1)
    return candidate


class LineParser(object):
    """Turns raw log lines into ``(epoch, LEVEL, text)``.

    A line with no timestamp of its own inherits the previous line's - that is what
    keeps a traceback attached to the message that introduced it, which is the whole
    reason to pull server logs next to a failing step.
    """

    def __init__(self, source):
        preset = resolve_format(source.format) or FORMATS[DEFAULT_FORMAT]
        # An explicit timestamp/level on the log wins over the preset - that is the
        # escape hatch for a backend no preset describes, and it must survive every
        # round trip through an editor (see cms_gui.logsourcesfile).
        stamp = source.timestamp or preset["timestamp"]
        # A log's own "tz" wins over the preset's and over the timestamp block's:
        # the shape of a line and the clock it was written by are two questions,
        # and only the second one changes per deployment.
        level = source.level or preset.get("level")
        try:
            self._stamp_re = re.compile(stamp["regex"])
        except (KeyError, TypeError, re.error) as exc:
            raise ServerLogError("log %r: bad timestamp regex (%s)" % (source.name, exc))
        self._stamp_format = stamp.get("format", "iso")
        self._tz = _tzinfo(source.tz or stamp.get("tz", "local"))
        self._level_re = None
        if level and level.get("regex"):
            try:
                self._level_re = re.compile(level["regex"])
            except re.error as exc:
                raise ServerLogError("log %r: bad level regex (%s)" % (source.name, exc))
        self._name = getattr(source, "name", "?")
        self._last_ts = None
        self._last_level = "INFO"
        self._drift_reported = False

    def _to_epoch(self, moment):
        if moment.tzinfo is not None:
            return moment.timestamp()
        if self._tz is None:
            return moment.timestamp()       # naive .timestamp() reads as local time
        return moment.replace(tzinfo=self._tz).timestamp()

    def timestamp_of(self, line, now=None):
        """The line's own epoch time, or None when it carries none."""
        match = self._stamp_re.search(line)
        if not match:
            return None
        text = match.group(1) if match.groups() else match.group(0)
        try:
            if self._stamp_format == "iso":
                moment = _parse_iso(text)
            elif self._stamp_format == "clf":
                moment = _parse_clf(text)
            else:
                moment = datetime.strptime(text, self._stamp_format)
        except ValueError:
            return None
        moment = _fill_missing_year(moment, now or datetime.now())
        try:
            return self._to_epoch(moment)
        except (OverflowError, OSError, ValueError):
            return None

    def level_of(self, line):
        if self._level_re is None:
            return None
        match = self._level_re.search(line)
        if not match:
            return None
        word = (match.group(1) if match.groups() else match.group(0)).strip()
        return normalize_level(word)

    def parse(self, line, now=None, seen_at=None):
        """``(epoch, LEVEL, text)`` for one raw line.

        ``seen_at`` is when the reader got the line, and it is the safety net. A
        line coming off a live tail was written moments ago - that is not a guess,
        it is what tailing means. So a timestamp claiming to be hours from now is
        a misread one, and trusting it would put every line outside every session's
        window and stream nothing at all, silently.
        """
        seen_at = time.time() if seen_at is None else seen_at
        text = line.rstrip("\n").rstrip("\r")
        moment = self.timestamp_of(text, now=now)
        if moment is None:
            # A continuation line: same event, same window, same level.
            moment = self._last_ts if self._last_ts is not None else seen_at
            level = self._last_level
        else:
            drift = moment - seen_at
            if abs(drift) > MAX_DRIFT_SECONDS:
                self._report_drift(drift)
                moment = seen_at
            self._last_ts = moment
            level = self.level_of(text) or "INFO"
            self._last_level = level
        return moment, normalize_level(level), text

    def _report_drift(self, drift):
        """Say once that this log's clock and this machine's do not agree."""
        if self._drift_reported:
            return
        self._drift_reported = True
        hours, rest = divmod(abs(int(drift)), 3600)
        minutes = rest // 60
        log.warning(
            "server log %r: its timestamps are %dh %02dm %s this machine's clock, "
            "so nothing would ever land in a session's window. Reading them as "
            "written now instead. Set \"tz\" on this log (\"utc\", or \"+HH:MM\") "
            "to read them properly - a backend logging in UTC on a machine that is "
            "not is the usual cause.",
            self._name, hours, minutes, "behind" if drift < 0 else "ahead of")





# -- correlation --------------------------------------------------------------
def floor_second(moment):
    """Round a window boundary down to the whole second it falls in.

    Log timestamps are only as precise as the format they were written in, and
    plenty are whole seconds (nginx, syslog, journalctl). A window that opens at
    12:00:00.75 would otherwise discard everything stamped "12:00:00" - which is
    every line that second, including the ones written after it opened - and the
    report for a scenario starting mid-second would come back empty.

    The cost is at most a second of lines from just before the boundary. That is a
    much better trade than losing the first second of the ones being looked for.
    """
    return math.floor(moment)


class SinceOpened(object):
    """Time-only correlation: a session sees what was written after it opened.

    Deliberately an object rather than a comparison inlined in the hub. A precise
    key - a header the profile's extension stamps on every request, a session id the
    backend prints - becomes another class with the same ``match``, and nothing that
    tails, parses or fans out has to change.
    """

    name = "since-opened"

    def match(self, entry, session):
        return entry.ts >= floor_second(session.opened_at)


# One parsed line. Plain and small: a busy stand produces a lot of these.
Entry = collections.namedtuple("Entry", "ts level text")

# One live window, as far as this module cares.
Session = collections.namedtuple("Session", "name env opened_at")


# -- readers ------------------------------------------------------------------
class _Reader(object):
    """Base: yields raw lines until told to stop."""

    def __init__(self, source):
        self.source = source

    def lines(self, stop):
        raise NotImplementedError

    def close(self):
        pass


class _ProcessReader(_Reader):
    """A follow command (tail/docker/journalctl), here or over ssh."""

    def __init__(self, source):
        _Reader.__init__(self, source)
        self._proc = None

    def argv(self):
        return self.source.connection.wrap(self.source.command())

    def lines(self, stop):
        proc = subprocess.Popen(
            self.argv(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, bufsize=1, universal_newlines=True)
        self._proc = proc
        try:
            for line in proc.stdout:
                if stop.is_set():
                    return
                yield line
            # End of stdout with nobody having asked to stop: the command died.
            # This HAS to be an error rather than a quiet return. An unknown ssh
            # host, a container that is not running and a path that does not
            # exist all end here in milliseconds, and a reader that simply
            # finishes makes every one of them look like a log that had nothing
            # to say - which is exactly the answer a "test this connection"
            # button must never give.
            if not stop.is_set():
                raise OSError(self._why_it_ended(proc))
        finally:
            self.close()

    def _why_it_ended(self, proc):
        """The message to report: ssh and docker explain themselves on stderr."""
        try:
            complaint = (proc.stderr.read() or "").strip()
        except (OSError, ValueError):
            complaint = ""
        try:
            code = proc.wait(timeout=5)
        except Exception:
            code = None
        if complaint:
            # One line, and preferably the one that says what went wrong. ssh
            # leads with "Warning: Identity file ... not accessible" and only
            # then reports that it could not resolve the host - reporting the
            # warning would send someone off fixing the wrong thing.
            lines = [line.strip() for line in complaint.splitlines() if line.strip()]
            real = [line for line in lines
                    if not line.lower().startswith(("warning:", "debug"))]
            return (real or lines)[0][:300]
        if code:
            return "%s exited with status %s" % (self.argv()[0], code)
        return "%s ended without producing anything" % self.argv()[0]

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class _LocalFileReader(_Reader):
    """Follows a local file directly, rather than shelling out to tail.

    Python can see both things that break a naive follow: a *rotation* (the path now
    points at a different inode) and a *truncation in place* (``: > app.log``, or a
    writer that reopens with "w"). It does it without a child process per window.

    Truncation is caught by fingerprinting the file's first bytes, not by watching
    the size. Size cannot answer it: between the truncate and the first write there
    is no moment to observe, so by the next poll the file is already longer than our
    read position and looks like ordinary growth - a race ``tail -F`` loses too. But
    a file that no longer *begins* with what it began with is a different file, and
    that holds however fast it was rewritten. Normal growth keeps its first bytes,
    so the check is quiet until something really replaces the content.

    Binary mode is not an accident either. ``TextIOWrapper.tell()`` returns an opaque
    cookie rather than a byte offset, so it cannot be compared against a file size.
    Reading bytes and decoding each line here keeps that comparison honest.
    """

    POLL_SECONDS = 0.25
    HEAD_BYTES = 256

    def __init__(self, source):
        _Reader.__init__(self, source)
        self._handle = None

    def _open(self, path, at_end):
        handle = open(path, "rb")
        if at_end:
            handle.seek(0, os.SEEK_END)     # only what happens from now on
        return handle

    def _inode(self, path):
        try:
            info = os.stat(path)
        except OSError:
            return None
        return (info.st_dev, info.st_ino)

    def _size(self, path):
        try:
            return os.stat(path).st_size
        except OSError:
            return None

    def _head(self, path):
        try:
            with open(path, "rb") as fh:
                return fh.read(self.HEAD_BYTES)
        except OSError:
            return None

    def lines(self, stop):
        path = os.path.expanduser(self.source.path)
        self._handle = self._open(path, at_end=True)
        state = (self._inode(path), self._head(path))
        # Resync *before* reading rather than after. The file can only change while
        # we are asleep, and reading first would hand out whatever bytes now sit at
        # our stale offset - the tail end of a rewritten line - before noticing.
        idled = False
        try:
            while not stop.is_set():
                if idled:
                    idled = False
                    state = self._resync(path, state)
                line = self._handle.readline()
                if line:
                    yield line.decode("utf-8", "replace")
                    continue
                idled = True
                stop.wait(self.POLL_SECONDS)
        finally:
            self.close()

    def _resync(self, path, state):
        """Reopen or rewind if the file changed identity while we waited."""
        inode, head = state
        current = self._inode(path)
        if current is None:             # rotated away and not back yet
            return state
        if current != inode:
            # Rotated: the name is a different file now, so read it whole - a fresh
            # log starts empty, which is what tail -F does too.
            self.close()
            self._handle = self._open(path, at_end=False)
            return current, self._head(path)
        now_head = self._head(path)
        size = self._size(path)
        # `startswith` is the whole test: an appended-to file still begins with its
        # old first bytes (and one that was empty still matches, since everything
        # starts with b""), while a rewritten or shortened one does not.
        replaced = (now_head is not None and head is not None
                    and not now_head.startswith(head))
        if replaced or (size is not None and size < self._handle.tell()):
            self._handle.seek(0)
            return current, now_head
        return current, (now_head if now_head else head)

    def close(self):
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


class _HttpReader(_Reader):
    """Reads a line-oriented HTTP stream. The connection plays no part here."""

    def __init__(self, source):
        _Reader.__init__(self, source)
        self._answer = None

    def lines(self, stop):
        from urllib.request import Request, urlopen
        request = Request(self.source.url, headers=dict(self.source.headers))
        self._answer = urlopen(request, timeout=30)
        try:
            for raw in self._answer:
                if stop.is_set():
                    break
                text = raw.decode("utf-8", "replace")
                # Server-sent events prefix each payload; anything else is the line.
                if text.startswith("data:"):
                    text = text[len("data:"):].lstrip()
                if text.strip():
                    yield text
        finally:
            self.close()

    def close(self):
        answer, self._answer = self._answer, None
        if answer is not None:
            try:
                answer.close()
            except Exception:
                pass


def read_lines(source, tail=READ_TAIL_LINES, max_bytes=READ_MAX_BYTES,
               timeout=30):
    """What the log holds right now: ``(lines, truncated)``. Not a follow.

    ``tail`` is how many lines from the end, or None for everything. Either way the
    byte budget wins, and ``truncated`` says the answer starts mid-log rather than
    at its beginning - which a reader has to be told, or they will read the first
    line they see as the first line there is.

    Raises OSError with what went wrong, because "why can I not read this" is the
    question being asked.
    """
    if source.type == "http":
        return _read_http(source, max_bytes, timeout)
    if source.type == "file" and source.connection.type == "local":
        return _read_local_file(source, tail, max_bytes)
    argv = source.connection.wrap(source.command(follow=False, tail=tail))
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise OSError("%s did not finish in %ds" % (argv[0], timeout))
    if proc.returncode and not out:
        complaint = (err or b"").decode("utf-8", "replace").strip()
        raise OSError(complaint.splitlines()[0][:300] if complaint
                      else "%s exited with status %s" % (argv[0], proc.returncode))
    truncated = len(out) > max_bytes
    if truncated:
        out = out[-max_bytes:]
    return _split(out, truncated)


def _read_local_file(source, tail, max_bytes):
    path = os.path.expanduser(source.path)
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        # From the end: the interesting part of a log is always its tail, and
        # reading a gigabyte to throw away all but the last screen is not a plan.
        start = max(0, size - max_bytes)
        fh.seek(start)
        data = fh.read()
    lines, truncated = _split(data, start > 0)
    if tail and len(lines) > tail:
        lines, truncated = lines[-tail:], True
    return lines, truncated


def _read_http(source, max_bytes, timeout):
    from urllib.request import Request, urlopen
    request = Request(source.url, headers=dict(source.headers))
    answer = urlopen(request, timeout=timeout)
    try:
        data = answer.read(max_bytes + 1)
    finally:
        answer.close()
    truncated = len(data) > max_bytes
    return _split(data[:max_bytes], truncated)


def _split(data, truncated):
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    lines = text.splitlines()
    # A budget cut lands mid-line; dropping that fragment is honester than showing
    # half a line as though it were one.
    if truncated and lines:
        lines = lines[1:]
    return lines, truncated


def make_reader(source):
    """The reader for one log. Split out so tests can put a fake in its place."""
    if source.type == "http":
        return _HttpReader(source)
    if source.type == "file" and source.connection.type == "local":
        return _LocalFileReader(source)
    return _ProcessReader(source)


# -- the hub ------------------------------------------------------------------
class _Stream(object):
    """One log's thread, buffer and pending batch."""

    def __init__(self, source, hub):
        self.source = source
        self.parser = LineParser(source)
        self.buffer = collections.deque(maxlen=hub.buffer_lines)
        self.pending = []
        self.dropped = 0
        self.unavailable = ""
        self.thread = None
        self._hub = hub


class ServerLogHub(object):
    """Tails every selected log and fans each line out to the sessions it belongs to.

    One thread per log, not per window: ten windows on one stand share the stand's
    one connection. Sessions register as their windows open; a line reaches a session
    when the log serves that session's environment and the correlation strategy says
    the line is the session's.
    """

    def __init__(self, sources, on_lines=None, correlation=None,
                 buffer_lines=BUFFER_LINES, batch_seconds=BATCH_SECONDS,
                 batch_max=BATCH_MAX_LINES, reader_factory=make_reader):
        self.buffer_lines = buffer_lines
        # A list, not a dict keyed by name: one run can span several environments
        # (no --env), and "app" legitimately names a log on each of them. Names are
        # unique *within* an environment, which is all the fan-out and the artifact
        # slicing need, since both filter by environment first.
        self._streams = []
        self._rejected = {}
        for source in sources:
            try:
                self._streams.append(_Stream(source, self))
            except ServerLogError as exc:
                # Building a stream compiles the log's patterns. A custom regex
                # with a typo in it must cost that one log, not the launch - the
                # same promise every other failure here keeps.
                log.warning("server log %r: %s. Skipping it.", source.name, exc)
                self._rejected[source.name] = str(exc)
        self._on_lines = on_lines
        self._correlation = correlation or SinceOpened()
        self._batch_seconds = batch_seconds
        self._batch_max = batch_max
        self._reader_factory = reader_factory
        self._sessions = collections.OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._flusher = None
        self._readers = []

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def sources(self):
        return [stream.source for stream in self._streams]

    def start(self):
        """Start one thread per log, plus the batch flusher. Idempotent."""
        if not self._streams or self._flusher is not None:
            return
        for stream in self._streams:
            stream.thread = threading.Thread(
                target=self._pump, args=(stream,),
                name="serverlog-%s" % stream.source.name, daemon=True)
            stream.thread.start()
        self._flusher = threading.Thread(
            target=self._flush_loop, name="serverlog-flush", daemon=True)
        self._flusher.start()

    def close(self):
        """Stop every thread and flush whatever was still pending."""
        self._stop.set()
        for reader in list(self._readers):
            try:
                reader.close()
            except Exception:
                pass
        self._readers = []
        for stream in self._streams:
            if stream.thread is not None:
                stream.thread.join(timeout=2)
                stream.thread = None
        if self._flusher is not None:
            self._flusher.join(timeout=2)
            self._flusher = None
        self._flush()

    # -- sessions ----------------------------------------------------------
    def add_session(self, name, env, opened_at=None):
        """Register a window. Lines written after ``opened_at`` become its own."""
        session = Session(name, env, opened_at if opened_at is not None else time.time())
        with self._lock:
            self._sessions[name] = session
        return session

    def drop_session(self, name):
        with self._lock:
            self._sessions.pop(name, None)

    def sessions_for(self, source):
        with self._lock:
            return [s for s in self._sessions.values() if source.serves(s.env)]

    # -- reading -----------------------------------------------------------
    def _pump(self, stream):
        """Read one log until told to stop, reconnecting a few times if it dies."""
        for attempt in range(_RETRIES + 1):
            if self._stop.is_set():
                return
            reader = self._reader_factory(stream.source)
            self._readers.append(reader)
            try:
                for line in reader.lines(self._stop):
                    self._ingest(stream, line)
                    if self._stop.is_set():
                        return
            except Exception as exc:
                # Diagnostics must never end a run: report once per attempt and
                # either come back or leave this one log marked unavailable.
                log.warning("server log %r: %s", stream.source.name, exc)
                stream.unavailable = str(exc)
            finally:
                try:
                    reader.close()
                except Exception:
                    pass
                if reader in self._readers:
                    self._readers.remove(reader)
            if self._stop.is_set() or attempt >= _RETRIES:
                break
            delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
            log.info("server log %r ended; retrying in %.0fs", stream.source.name, delay)
            self._stop.wait(delay)
        if not self._stop.is_set():
            log.warning("server log %r gave up after %d attempts",
                        stream.source.name, _RETRIES + 1)
            stream.unavailable = stream.unavailable or "stream ended"

    def _ingest(self, stream, line):
        entry = Entry(*stream.parser.parse(line))
        if not entry.text.strip():
            return
        with self._lock:
            stream.buffer.append(entry)
            if len(stream.pending) < self._batch_max:
                stream.pending.append(entry)
            else:
                # The buffer above still has it; only the screen is rate limited.
                stream.dropped += 1

    # -- batching ----------------------------------------------------------
    def _flush_loop(self):
        while not self._stop.wait(self._batch_seconds):
            self._flush()

    def _flush(self):
        if self._on_lines is None:
            return
        for stream in self._streams:
            with self._lock:
                pending, stream.pending = stream.pending, []
                dropped, stream.dropped = stream.dropped, 0
                sessions = [s for s in self._sessions.values()
                            if stream.source.serves(s.env)]
            if not pending and not dropped:
                continue
            for session in sessions:
                lines = [entry for entry in pending
                         if self._correlation.match(entry, session)]
                if dropped:
                    lines.append(Entry(time.time(), "WARN",
                                       "... %d lines dropped (rate limit)" % dropped))
                if not lines:
                    continue
                try:
                    self._on_lines(session.name, stream.source.name, lines)
                except Exception:       # a consumer must not break the tail
                    pass

    # -- for the report artifact -------------------------------------------
    def slice(self, env, start, end=None):
        """``{log name: [text]}`` for one environment and time window.

        Straight off the ring buffer, so a scenario's file holds every line the tail
        saw in its window - the event stream's per-batch ceiling does not apply here.
        """
        end = time.time() if end is None else end
        # Same second-resolution reasoning as the correlation window: a scenario
        # that starts at 12:00:00.75 must still get the lines a whole-second log
        # stamped "12:00:00", and the ones it wrote as the scenario ended.
        start, end = floor_second(start), math.ceil(end)
        out = collections.OrderedDict()
        with self._lock:
            for stream in self._streams:
                if not stream.source.serves(env):
                    continue
                lines = [entry.text for entry in stream.buffer
                         if start <= entry.ts <= end]
                if lines:
                    out[stream.source.name] = lines
        return out

    def slice_for_session(self, session_name, start, end=None):
        """:meth:`slice` for the environment a registered window belongs to.

        The caller that wants this - the runner, writing one scenario's report -
        knows the session name and nothing about environments, and the hub already
        recorded the pairing when the window opened. An unknown name answers empty
        rather than raising: a report is a diagnostic, not a place to fail.
        """
        with self._lock:
            session = self._sessions.get(session_name)
        if session is None:
            return collections.OrderedDict()
        return self.slice(session.env, start, end)

    def unavailable(self):
        """``{log name: reason}`` for the logs that could not be read.

        Includes the ones rejected before they ever started - a pattern that would
        not compile is exactly as unavailable as a host that will not answer.
        """
        answer = dict(self._rejected)
        answer.update({stream.source.name: stream.unavailable
                       for stream in self._streams if stream.unavailable})
        return answer
