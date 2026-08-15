#!/usr/bin/env python3
"""Open one isolated Chrome window per test user.

Each --user-data-dir is a separate Chrome profile, so every user can be logged
in at the same time. A tiny generated extension auto-fills and submits the
login form per profile, using that user's own password (once per tab, so wrong
credentials are never resubmitted in a loop).

The user list lives in a JSON config file (users.json next to this script by
default; override with --config=FILE). Each entry carries its own login, window
class and password, plus an optional env (see below). Run --init-users-json
once to scaffold a starter config (it won't overwrite an existing one). Launch a subset
with --env=NAME and/or --filter-users=login1,login2 (defaults = everything).
To launch a single user without editing the config, pass --user=LOGIN
(with --password=PASS when the config does not already know that login).

The "env" field is the environment a user belongs to - a URL or host:port, e.g.
"localhost:8069" or "https://app-dev.example.com/". --env selects one by short
name (--env=dev matches "app-dev") and supplies that environment's URL, so --url
only has to be typed to override it. Everything --env implies is a default: any
flag you pass explicitly wins over it.

Each user's profile folder is "<env>-<login>", so the env+login pair must be
unique (the same login can repeat under different envs - separate environments).
--user-session overrides the folder's prefix for the whole run.

By default the script stays in the foreground and holds a reference to every
launched window; press CTRL+C to close all of them at once. Pass --detach to
fire-and-forget instead (windows survive the script exiting, old behavior).

Profiles + generated extensions live under a sessions dir (user_sessions next to
this script by default; override with --sessions-dir=DIR).

Pass --user-session=PREFIX to override every profile folder's prefix under the
sessions dir. This keeps separate sessions from sharing logins. The per-user
login is appended automatically.

The URL can be passed as --url=URL or as a positional argument.

Each profile also gets that user's single login/password saved in Chrome's own
password manager, encrypted with this platform's Chrome key: on Linux the windows
run with --password-store=basic so cookies and saved passwords share one
deterministic key, while on macOS the key comes from the login Keychain ("Chrome
Safe Storage") - the first run there pops a Keychain prompt, choose Always Allow.

The auto-login helper is installed straight into each profile (files under
Default/Extensions plus a Preferences entry) rather than via --load-extension, which
recent Chrome (137+) blocks. By default every usable extension vendored in
extensions/ is installed the same way (a broken one is skipped with a warning, never
stopping the launch). --extensions overrides that: "none" for nothing, or a
comma-separated list of local names, Chrome Web Store names/ids, or name=id.
--extensions=list shows what is available.

Usage: python session_launcher.py [--detach] [--env=NAME] [--user-session=PREFIX]
           [--sessions-dir=DIR] [--config=FILE] [--extensions=LIST]
           [--user=LOGIN --password=PASS] (--env=NAME | --url=URL | <URL>)
   e.g. python session_launcher.py --env=dev
        python session_launcher.py --url=http://localhost:8069/web/login
"""
import atexit
import base64
import collections
import ctypes
import ctypes.util
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import runtime_paths

log = logging.getLogger("session_launcher")

# engine.events once --events is given, else None. The module is imported lazily
# (a plain launch must not pay for the engine package), but the window-lifecycle
# emitters below are module-level functions, so they reach it through here.
_events = None


def _emit(kind, **fields):
    """Emit a launcher lifecycle event; a no-op unless --events was given."""
    if _events is not None:
        _events.emit(kind, **fields)


# Where things live. In a source checkout every one of these is the checkout
# itself, so the project stays self-contained and movable; in an installed build
# the read-only resources come out of the bundle and everything writable moves to
# ~/ChromeMultiSession, so an upgrade cannot touch it. See runtime_paths.
# The sessions dir and the user config file can be overridden on the command line.
SCRIPT_DIR = runtime_paths.app_root()


def version():
    """The installed version, or "dev" when running from a source checkout.

    pyproject.toml holds the only copy; this reads it back rather than keeping a
    second one here to drift out of step. A frozen build has no installed
    distribution to look up, so it falls back to the VERSION file the build script
    writes into the bundle from that same pyproject.toml.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version as _version
        return _version("chrome-multi-session")
    except Exception:
        return runtime_paths.bundled_version() or "dev (not installed)"


DEFAULT_SESSIONS_DIR = runtime_paths.sessions_dir()
DEFAULT_CONFIG = runtime_paths.config_path()
# Unpacked extensions kept in-tree: extensions/<name>/manifest.json. Installed with
# no network access at all, and editable - see extensions/README.md.
EXTENSIONS_DIR = runtime_paths.extensions_dir()

# Where a bare URL should land, and which paths count as "the login page".
#
# The launcher opens the signed-in entry point rather than the login form, so an
# existing session is reused; with no session the app redirects to its own login
# page, where the auto-login extension takes over. The defaults suit apps that serve
# their client from /web - change these two for an app that uses other paths.
LANDING_PATH = "/web"
LOGIN_PATHS = ("", "/", "/web/login", "/web/login/")

# Starter config written by --init-users-json: one example user with an inline
# _comment doc (the loader ignores keys it doesn't use). The config holds
# passwords and is git-ignored, so this scaffolds a fresh copy to edit.
#
# Built with json.dumps rather than written out as a string: this file must load
# as-is, and hand-quoted JSON is exactly how it stopped doing so before. Note
# "run-tests" is OMITTED, not written empty - an empty list is rejected, on the
# grounds that a field meaning nothing should not be there. See
# test_scaffolded_config_loads, which scaffolds and then loads.
_USERS_TEMPLATE_ENTRY = {
    "_comment": (
        "one object per user - env=the environment this user belongs to (a URL or "
        "host:port; --env selects it, and it also names the profile folder), "
        "class=window label, login=username, password=that user's password. Add an "
        "optional \"run-tests\": [\"scenario_id\"] to give this user its own scenarios "
        "under --run-tests=config. The env+login pair names the folder and must be "
        "unique. Copy this line per user."
    ),
    "env": "localhost:8069",
    "class": "User 1",
    "login": "login1",
    "password": "secret1",
}
DEFAULT_USERS_TEMPLATE = json.dumps([_USERS_TEMPLATE_ENTRY], indent=2) + "\n"


def init_users_json(config_path):
    """Write a starter users.json to config_path, then exit.

    Creates the file only when it does not already exist, so real credentials
    are never overwritten.
    """
    if os.path.exists(config_path):
        sys.exit("%s already exists; not overwriting." % config_path)
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(DEFAULT_USERS_TEMPLATE)
    sys.exit("Wrote starter config to %s - edit it, then run again." % config_path)


# One configured user. `tests` is that user's own scenario ids, run by
# --run-tests=config so a role does not have to be paired with its test by hand.
User = collections.namedtuple("User", "env cls login password tests")
User.__new__.__defaults__ = ((),)   # tests is optional

# Config paths already nudged about the old "prefix" field name (once per run).
_WARNED_LEGACY = set()


def _parse_tests_field(value, where, field):
    """Normalize a config "run-tests" value to a tuple of scenario ids.

    Accepts a JSON list or a comma-separated string, and splits commas inside list
    items too, so every spelling below means the same thing - matching how
    --run-tests reads on the command line:

        "run-tests": ["access_agent", "tag:repro"]
        "run-tests": "access_agent,tag:repro"
        "run-tests": ["access_agent,tag:repro"]
    """
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        sys.exit("%s: %r must be a list or a comma-separated string, got %r"
                 % (where, field, value))
    ids = []
    for item in value:
        if not isinstance(item, str):
            sys.exit("%s: %r entries must be scenario ids, got %r" % (where, field, item))
        for part in item.split(","):
            part = part.strip()
            if not part:
                continue
            # The engine's selector is `tag:NAME`, one tag per entry - "tags:" (or
            # several tags in one entry) silently becomes a scenario id that does
            # not exist, and the run fails much later with "flow not found".
            if part.startswith("tags:"):
                sys.exit("%s: %r has %r; the tag selector is singular - use %r, one "
                         "tag per entry." % (where, field, part, "tag:" + part[len("tags:"):]))
            ids.append(part)
    if not ids:
        sys.exit("%s: %r is empty; drop the field instead." % (where, field))
    return tuple(ids)


def _read_config(config_path):
    """Read and parse the users JSON file; raises OSError/ValueError on failure."""
    with open(config_path, encoding="utf-8") as fh:
        return json.load(fh)


def load_users(config_path):
    """Load the user list from a JSON config file.

    The file is a JSON array of objects, each with class (window class / profile
    label), login and password, plus an optional env - the environment the user
    belongs to (--env selects it; --user-session overrides the folder name it
    produces). Every user carries its own password - there is no shared default.
    The profile folder is <env>-<login>, so that pair must be unique. Returns a
    list of (env, class, login, password) tuples.

    "prefix" is still accepted as the old name for "env".
    """
    try:
        data = _read_config(config_path)
    except (OSError, ValueError) as exc:
        sys.exit("Could not read config %s: %s" % (config_path, exc))
    if not isinstance(data, list) or not data:
        sys.exit("Config %s must be a non-empty JSON array of users." % config_path)
    users = []
    seen = set()
    legacy = False
    for i, entry in enumerate(data):
        try:
            env = entry.get("env", entry.get("prefix", ""))  # optional
            login = entry["login"]
            # "run-tests" mirrors the CLI flag; "tests" is accepted as a shorthand.
            field = "run-tests" if "run-tests" in entry else "tests"
            tests = _parse_tests_field(entry.get(field), "Config entry %d" % i, field)
            record = User(env, entry["class"], login, entry["password"], tests)
        except (KeyError, TypeError, AttributeError):
            sys.exit("Config entry %d must have class, login and password: %r" % (i, entry))
        # Both spellings on one entry is a typo we must not paper over: picking
        # either one silently files the profile under the wrong folder.
        if "env" in entry and "prefix" in entry and entry["env"] != entry["prefix"]:
            sys.exit("Config entry %d has both 'env' (%r) and the old 'prefix' (%r); "
                     "keep only 'env'." % (i, entry["env"], entry["prefix"]))
        legacy = legacy or ("env" not in entry and "prefix" in entry)
        # The profile folder is <env>-<login>, so that pair must be unique. The
        # same login under different envs is fine (that is the whole point).
        if (env, login) in seen:
            sys.exit("Config has duplicate env+login %r / %r; that pair names "
                     "the profile folder and must be unique." % (env, login))
        seen.add((env, login))
        users.append(record)
    if legacy and config_path not in _WARNED_LEGACY:
        # load_users runs more than once per launch (environment list, then rows);
        # the nudge is per config file, not per read.
        _WARNED_LEGACY.add(config_path)
        log.info("%s still uses the old 'prefix' field; it now reads as 'env' - "
                 "rename it when convenient.", config_path)
    return users


def session_dir_for(session_prefix, entry_env, login):
    """Build the profile-folder name "<env>-<login>" as one safe path segment.

    --user-session (session_prefix) overrides the config's per-entry env. Path
    separators in the env (e.g. from a URL like "https://host/") are flattened so
    the result stays a single directory; ":" and the like are kept. The login is
    sanitized the same way ad-hoc --user profiles are.

    On Windows the characters NTFS rejects go too - "localhost:8069" cannot be a
    folder name there. That rule is deliberately platform-conditional: applying it
    everywhere would rename every profile folder an existing Linux install already
    has, silently orphaning its logged-in sessions.
    """
    prefix = session_prefix or entry_env
    safe_prefix = re.sub(r"[/\\]+", "_", prefix)
    if os.name == "nt":
        safe_prefix = re.sub(r'[:*?"<>|]+', "_", safe_prefix).rstrip(". ")
    safe_login = re.sub(r"[^A-Za-z0-9._-]", "_", login) or "user"
    return "%s-%s" % (safe_prefix, safe_login) if safe_prefix else safe_login


# One environment, derived from the distinct "env" values in the config: alias is
# the short name --env matches, value is the config string (which also names the
# profile folder), origin is the URL --env supplies, count is how many users use it.
Env = collections.namedtuple("Env", "alias value origin count")

# Hosts that are served over plain http when the env value carries no scheme.
_LOCAL_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))


def split_env_value(value):
    """urlsplit an env value, which may or may not carry a scheme.

    urlsplit("localhost:8069") reads "localhost" as the SCHEME and leaves netloc
    empty - so a bare host:port has to be parsed protocol-relative instead, or
    every localhost env resolves to the nonsense origin "localhost://".
    """
    return urlsplit(value if "://" in value else "//" + value)


def _is_host_like(split):
    """True when an env value looks addressable (so it can supply a URL).

    A plain label like "myprofile" is a legal env value - it names the profile
    folder - but there is no URL to derive from it.
    """
    host = split.hostname
    if not host:
        return False
    return bool(split.scheme) or split.port is not None or "." in host or host in _LOCAL_HOSTS


def env_origin(value):
    """Derive the origin an env points at ("" when the value is not host-like).

    No trailing slash, port preserved - the same shape as the origin computed from
    --url, so the two can be compared directly. Local hosts default to http, every
    other host to https.
    """
    split = split_env_value(value)
    if not _is_host_like(split):
        return ""
    scheme = split.scheme
    if not scheme:
        host = split.hostname
        scheme = "http" if host in _LOCAL_HOSTS or host.endswith(".localhost") else "https"
    return "%s://%s" % (scheme, split.netloc)


def env_alias(value):
    """Short name for an env value: the host's first DNS label, lowercased.

    "https://app-dev.example.com/" -> "app-dev", "localhost:8069" -> "localhost".
    IP literals keep the whole host, so 127.0.0.1 does not collapse to "127".
    """
    split = split_env_value(value)
    host = (split.hostname or value.strip()).lower()
    if ":" in host or re.fullmatch(r"[0-9.]+", host):
        return host  # IPv6 literal or IPv4 address - the labels are not names
    return host.split(".")[0]


def build_environments(values):
    """Turn the config's env values into a list of Env, in first-seen order.

    Aliases must be unique for --env to resolve, so any collision (two hosts
    sharing a first label, e.g. localhost:8069 and localhost:8070) falls back to
    the full netloc for that group, then to the raw value.
    """
    counts = collections.OrderedDict()
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    envs = [Env(env_alias(v), v, env_origin(v), n) for v, n in counts.items()]
    by_alias = collections.defaultdict(list)
    for env in envs:
        by_alias[env.alias].append(env)
    for alias, group in by_alias.items():
        if len(group) < 2:
            continue
        for i, env in enumerate(group):
            netloc = split_env_value(env.value).netloc.lower()
            retry = netloc if netloc and netloc != alias else env.value
            envs[envs.index(env)] = env._replace(alias=retry)
    aliases = [e.alias for e in envs]
    assert len(aliases) == len(set(aliases)), "env aliases must be unique: %r" % (aliases,)
    return envs


def environments_from_config(config_path, strict=True):
    """Build the environment list from a config file.

    strict=False returns [] instead of exiting when the config cannot be read, for
    the paths where the config is a nice-to-have (an ad-hoc --user run that was
    given its own --password) rather than the thing being asked for.
    """
    if strict:
        return build_environments([u.env for u in load_users(config_path)])
    try:
        data = _read_config(config_path)
    except (OSError, ValueError) as exc:
        log.debug("Could not read %s for environment names: %s", config_path, exc)
        return []
    if not isinstance(data, list):
        return []
    values = []
    for entry in data:
        if isinstance(entry, dict):
            values.append(entry.get("env", entry.get("prefix", "")) or "")
    return build_environments(values)


def describe(config_path, flows_dir=None, sessions_dir=None, reports_dir=None):
    """Everything a front-end needs to populate its pickers, as one dict.

    ``--describe`` prints this as JSON and exits. It exists so a GUI never has to
    re-implement the config and flow parsing that lives here: the launcher stays
    the single source of truth for what environments, users, scenarios and
    extensions exist, and a change here reaches every front-end for free.

    Passwords are deliberately absent - only ``has_password`` says whether one is
    configured. Anything that cannot be read degrades to an empty list plus an
    entry in ``warnings``, so one broken piece never denies the caller the rest.
    """
    warnings = []
    users, envs = [], []
    try:
        rows = load_users(config_path)
    except SystemExit as exc:          # load_users reports config errors this way
        rows = []
        warnings.append(str(exc))
    for user in rows:
        users.append({"env": user.env, "class": user.cls, "login": user.login,
                      "tests": list(user.tests), "has_password": bool(user.password),
                      "profile": session_dir_for("", user.env, user.login)})
    for env in build_environments([u.env for u in rows]):
        envs.append({"alias": env.alias, "value": env.value,
                     "origin": env.origin, "count": env.count})

    scenarios, blocks, selectors = [], [], {}
    # ``flows_dir`` stays None unless it was given, so the loader resolves it the
    # way a run will - the user's own tree, then the bundled one. Pinning it to a
    # single directory here would hide every scenario the user has written and,
    # worse, make the bundled tree look writable to the editor. What gets
    # reported is where a new scenario would be written.
    reported_flows_dir = flows_dir or runtime_paths.user_flows_dir()
    try:
        # Lazy: the loader needs pyyaml, which a plain launch never installs.
        from engine import flowfile, loader
        skipped = loader._SKIP_TAGS
        for scenario_id in loader.discover_scenarios(flows_dir, include_templates=True):
            try:
                flow = loader.load_flow(scenario_id, flows_dir)
            except Exception as exc:
                warnings.append("scenario %s: %s" % (scenario_id, exc))
                continue
            tags = list(flow.tags)
            # Which tree it came from decides whether the editor may touch it:
            # anything bundled with the app is replaced on the next upgrade.
            writable = flowfile.is_writable(flow.source, flows_dir)
            scenarios.append({"id": scenario_id, "name": flow.name,
                              "description": flow.description, "tags": tags,
                              "path": flow.source,
                              "source": "user" if writable else "bundled",
                              "writable": writable,
                              # what --run-tests=all would actually run
                              "in_all": not (set(tags) & skipped)})
        # The blocks a scenario reaches through `use:`. Not runnable on their own,
        # so they are not scenarios - but an editor showing `use: access.open_app`
        # with no way to open it is showing an alias with nothing behind it.
        for block_id, path in sorted(loader.block_files(flows_dir).items()):
            try:
                flow = loader.load_flow(block_id, flows_dir)
            except Exception as exc:
                warnings.append("block %s: %s" % (block_id, exc))
                continue
            writable = flowfile.is_writable(path, flows_dir)
            blocks.append({"id": block_id, "name": flow.name,
                           "description": flow.description,
                           "tags": list(flow.tags), "path": path,
                           "source": "user" if writable else "bundled",
                           "writable": writable})
        # name -> selector, merged across trees, so the editor can say what
        # `click: menu_settings` will actually look for.
        selectors = loader.load_selectors(flows_dir)
    except ImportError as exc:
        warnings.append("scenarios unavailable (%s); install the 'flows' extra." % exc)

    extensions = []
    for name, path in sorted(local_extensions().items()):
        ok, reason = validate_local_extension(path)
        extensions.append({"name": name, "kind": "local", "value": path,
                           "usable": ok, "reason": reason})
    for name, ext_id in sorted(KNOWN_EXTENSIONS.items()):
        extensions.append({"name": name, "kind": "store", "value": ext_id,
                           "usable": True, "reason": ""})

    return {
        "version": version(),
        "script": os.path.abspath(__file__),
        "config_path": config_path,
        "flows_dir": reported_flows_dir,
        "reports_dir": reports_dir or runtime_paths.reports_dir(),
        "sessions_dir": sessions_dir or DEFAULT_SESSIONS_DIR,
        # The front-end needs to be able to say "install Chrome" before the user
        # tries to launch anything, so the answer travels with the inventory.
        "chrome": describe_chrome(),
        "log_levels": ["DEBUG", "INFO", "WARNING", "ERROR"],
        # The step grammar, straight from the compiler, so a scenario editor can
        # offer exactly the actions that exist and know the shape of each one's
        # arguments without keeping its own copy of the list to drift out of step.
        "flow_actions": flow_actions(),
        "overlay_components": list(_OVERLAY_COMPONENTS),
        "report_artifacts": list(_REPORT_ARTIFACTS),
        "report_screen_modes": list(_REPORT_SCREEN_MODES),
        "tags": sorted({tag for s in scenarios for tag in s["tags"]}),
        "envs": envs,
        "users": users,
        "scenarios": scenarios,
        "blocks": blocks,
        "selectors": selectors,
        "extensions": extensions,
        "warnings": warnings,
    }


def flow_actions():
    """Every step action, grouped by the shape of its arguments.

    Empty when the engine is not installed - a plain launch never needs it, and
    --describe degrades rather than failing when the flows extra is missing.
    """
    try:
        from engine import compiler
    except ImportError:
        return {}
    return {
        # target only, and the target is a named selector or raw CSS
        "selector_only": sorted(compiler.SELECTOR_ONLY),
        # target and value both required
        "selector_and_value": sorted(compiler.SELECTOR_AND_VALUE),
        # value only; there is nothing to point at
        "value_only": sorted(compiler.VALUE_ONLY),
        # target is a URL, never a selector
        "url_target": sorted(compiler.URL_TARGET),
        # composition: the target is another flow's id
        "use": [compiler.USE],
        # accepted by wait_for; anything else is Playwright's default
        "states": ["visible", "attached", "hidden", "detached"],
    }


def run_flow_command(command, source, flows_dir=None):
    """Serve one --flow-show/save/delete/import call: print JSON, then exit.

    The scenario file format belongs to the engine, and the GUI depends on PySide6
    and nothing else - it cannot read or write YAML. So these four commands are
    the whole of the editing surface, and like --describe they always answer with
    JSON, even on failure: the caller is a program, and an exit code plus a
    plain-text message would leave it parsing error strings.
    """
    kind, argument = command
    try:
        # Lazy, like every other engine import here: a launch must not pay for
        # pyyaml just because these flags exist.
        from engine import flowfile
        if kind == "show":
            payload = flowfile.describe_flow(argument, flows_dir)
        elif kind == "delete":
            payload = flowfile.delete(argument, flows_dir)
        elif kind == "import":
            payload = flowfile.import_file(argument, flows_dir)
        elif kind == "save":
            payload = _save_flow(argument, source, flows_dir)
        elif kind == "selectors-show":
            payload = flowfile.describe_selectors(flows_dir)
        elif kind == "selectors-save":
            payload = _save_selectors(source, flows_dir)
        else:                                   # unreachable; kept honest anyway
            raise ValueError("unknown flow command %r" % kind)
    except Exception as exc:  # noqa: BLE001 - the report IS the error report
        json.dump({"ok": False, "id": argument,
                   "problems": ["%s: %s" % (type(exc).__name__, exc)]},
                  sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
        sys.exit(2)
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()
    # "ok" is absent from a --flow-show payload, which is a read and cannot fail
    # here; only the writes report one.
    sys.exit(0 if payload.get("ok", True) else 1)


def _save_flow(flow_id, source, flows_dir=None):
    """--flow-save: read the JSON document at ``source`` and write the scenario.

    The document is either ``{"yaml": "..."}`` - the text someone typed - or
    ``{"meta": {...}, "steps": [...]}``, which is what a step editor and the
    recorder produce. One writer either way, so both go through the same
    validation and land in the same shape on disk.
    """
    from engine import flowfile
    if not source:
        return {"ok": False, "id": flow_id,
                "problems": ["--flow-save needs --from=FILE (a JSON document)"]}
    try:
        with open(source, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        return {"ok": False, "id": flow_id,
                "problems": ["cannot read %s: %s" % (source, exc)]}
    if not isinstance(document, dict):
        return {"ok": False, "id": flow_id,
                "problems": ["%s must contain a JSON object" % source]}
    return flowfile.save(flow_id, flows_dir,
                         yaml_text=document.get("yaml"),
                         meta=document.get("meta"),
                         steps=document.get("steps"))


def _save_selectors(source, flows_dir=None):
    """--selectors-save: write the named-target map from a JSON {"yaml": ...}."""
    from engine import flowfile
    if not source:
        return {"ok": False,
                "problems": ["--selectors-save needs --from=FILE (a JSON document)"]}
    try:
        with open(source, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        return {"ok": False, "problems": ["cannot read %s: %s" % (source, exc)]}
    if not isinstance(document, dict) or "yaml" not in document:
        return {"ok": False,
                "problems": ["%s must contain a JSON object with a \"yaml\" key"
                             % source]}
    return flowfile.save_selectors(document["yaml"], flows_dir)


def _shortening_example(envs):
    """An alias shortening that really is unambiguous, for the help text.

    Prefers the tail of a hyphenated alias ("app-dev" -> "dev"), which is how
    these get typed in practice; falls back to a unique 3-letter head.
    """
    for env in sorted(envs, key=lambda e: "-" not in e.alias):  # hyphenated first
        for candidate in (env.alias.rsplit("-", 1)[-1], env.alias[:3]):
            if candidate != env.alias and sum(candidate in e.alias for e in envs) == 1:
                return candidate, env.alias
    return None, None


def format_environments(envs, config_path):
    """Render the known environments as a help block (used by every env error)."""
    if not envs:
        return "No environments are defined in %s (no 'env' values)." % config_path
    alias_w = max(len(e.alias) for e in envs)
    origin_w = max(len(e.origin or "-") for e in envs)
    lines = ["Known environments (from %s):" % config_path]
    for env in sorted(envs, key=lambda e: e.alias):
        lines.append("  %-*s   %-*s   env %r  (%d user%s)"
                     % (alias_w, env.alias, origin_w, env.origin or "-", env.value,
                        env.count, "" if env.count == 1 else "s"))
    short, full = _shortening_example(envs)
    if short:
        lines.append("Any unambiguous shortening works, e.g. --env=%s for %s." % (short, full))
    return "\n".join(lines)


def resolve_environment(name, envs, config_path):
    """Resolve --env to exactly one Env, or exit with the list of known ones.

    Staged: exact match on alias/value/origin/host first (so an old --filter-prefix
    string still works verbatim), then alias prefix, then alias substring. A stage
    that matches several is ambiguous and stops there rather than falling through -
    silently picking one is how the wrong windows get launched.
    """
    listing = format_environments(envs, config_path)
    wanted = name.strip().lower()
    if not wanted:
        sys.exit("--env requires a name, e.g. --env=dev.\n%s" % listing)
    stages = (
        lambda e: wanted in (e.alias, e.value.lower(), e.origin.lower(),
                             (split_env_value(e.value).hostname or "").lower()),
        lambda e: e.alias.startswith(wanted),
        lambda e: wanted in e.alias,
    )
    for match in stages:
        hits = [e for e in envs if match(e)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            sys.exit("--env=%r matches %d environments: %s. Use a longer name.\n%s"
                     % (name, len(hits), ", ".join(sorted(e.alias for e in hits)), listing))
    extra = ""
    if wanted == "all":
        extra = ("\n--env selects exactly one environment; omit it to launch every "
                 "environment at once.")
    sys.exit("--env: no environment matches %r.\n%s%s" % (name, listing, extra))


def normalize_url(url):
    """Point a bare/login URL at /web; the app redirects to the login form itself.

    If we go to /web the app uses the saved session; with no session it redirects
    to its login page, where our extension logs us in.
    """
    split = urlsplit(url)
    path = split.path
    if path in LOGIN_PATHS:
        path = LANDING_PATH
    return split._replace(path=path).geturl()


def parse_filter_list(flag, value):
    """Parse a comma-separated --filter-* value; None means "no filtering".

    "all" (or the flag being absent) keeps everything, but an explicitly EMPTY
    value is an error: --filter-users="$VAR" with an unset VAR used to silently
    mean "all", which is how a one-user run turned into every user in the config.
    """
    cleaned = [part.strip() for part in value.split(",") if part.strip()]
    if not cleaned:
        sys.exit('%s= is empty. Omitting the flag already means "all"; pass %s=all to keep '
                 "everyone, or --env=NAME to pick one environment. (An unset shell variable "
                 "is the usual cause.)" % (flag, flag))
    if len(cleaned) == 1 and cleaned[0].lower() == "all":
        return None
    return cleaned


def lookup_scope(selected_env, origin, envs):
    """Which env's rows an ad-hoc --user is resolved against ("" = the whole config).

    --env picks it outright; failing that, a --url whose origin belongs to a known
    environment picks that one, so `--user=X --url=<dev>` still lands in dev.
    """
    if selected_env is not None:
        return selected_env.value
    for env in envs:
        if env.origin and env.origin.lower() == (origin or "").lower():
            return env.value
    return ""


def env_shared_password(users, scope):
    """The password an environment uses, i.e. the one a majority of its rows share.

    Test environments are provisioned with one password across their users (with
    the odd admin outlier), so a login that is not in the config can still be
    launched with the environment's own credentials. None when there is no strict
    majority - then the caller has to ask for --password.
    """
    rows = [u for u in users if not scope or u.env == scope]
    if not rows:
        return None
    counts = collections.Counter(u.password for u in rows)
    password, hits = counts.most_common(1)[0]
    if hits * 2 <= len(rows):
        return None  # no strict majority (e.g. a 1-1 split) - do not guess
    return password, hits, len(rows)


def adhoc_session_prefix(selected_env, origin, envs, url):
    """Folder prefix for an ad-hoc --user run, so its profile is per-environment.

    Ad-hoc profiles used to be the bare login, which meant one folder shared by
    every environment: running the same login against dev and then localhost
    overwrote the session each time. Reuse the environment's own config value so
    an ad-hoc run and a config-driven run for the same user+env share one profile.
    """
    scope = lookup_scope(selected_env, origin, envs)
    if scope:
        return scope
    return urlsplit(url).netloc


def resolve_user_row(cli_user, cli_password, users, selected_env, origin, envs,
                     config_path, url):
    """Build the single (env, class, login, password) row for an ad-hoc --user run.

    --env supplies the record and --user overrides its login, so the password comes
    from the environment unless one was passed explicitly:
      1. --password wins outright.
      2. Otherwise the login's own config row (its class comes along too).
      3. Otherwise the environment's shared password - a login missing from the
         config is not an error, the env still holds the credentials for it.
    """
    session_prefix = adhoc_session_prefix(selected_env, origin, envs, url)
    if cli_password is not None:
        return User(session_prefix, cli_user, cli_user, cli_password)

    scope = lookup_scope(selected_env, origin, envs)
    rows = [u for u in users if u.login == cli_user and (not scope or u.env == scope)]
    if len(rows) == 1:
        row = rows[0]
        return row._replace(env=session_prefix or row.env)
    if len(rows) > 1:
        # Same login in several environments and nothing to pick between them.
        sys.exit("--user=%s exists in %d environments (%s). Pass --env to choose one, "
                 "or --password to launch it ad-hoc.\n%s"
                 % (cli_user, len(rows), ", ".join(env_alias(u.env) for u in rows),
                    format_environments(envs, config_path)))

    shared = env_shared_password(users, scope)
    if shared is None:
        where = "%r" % scope if scope else "the config"
        sys.exit("--user=%s is not in %s for %s, and its users do not share one password. "
                 "Pass --password." % (cli_user, config_path, where))
    password, hits, total = shared
    log.info("%r is not in %s for %s; using the password shared by %d of its %d users.",
             cli_user, os.path.basename(config_path),
             selected_env.alias if selected_env else (scope or "the config"), hits, total)
    return User(session_prefix, cli_user, cli_user, password)


def apply_password_override(users, cli_password):
    """Replace every selected row's password with an explicitly passed --password.

    Same precedence as everywhere else: what you typed beats what the config holds.
    """
    return [u._replace(password=cli_password) for u in users]


def _bad_option_message(arg, config_path):
    """Explain an option the parser cannot use, with a hint for the known cases.

    Every one of these used to fall through to the positional URL slot, so the
    launcher opened Chrome on the flag itself instead of failing.
    """
    if arg.startswith("--filter-prefix"):
        return "--filter-prefix was replaced by --env.\n%s" % format_environments(
            environments_from_config(config_path, strict=False), config_path)
    if arg == "--env":
        return "--env needs a value, e.g. --env=dev.\n%s" % format_environments(
            environments_from_config(config_path, strict=False), config_path)
    if arg == "--run-tests":
        return ("--run-tests needs a value:\n"
                "  --run-tests=all            every scenario in flows/scenarios\n"
                "  --run-tests=config         each user's own 'run-tests' field\n"
                "  --run-tests=ID[,ID...]     named scenarios, or tag:NAME")
    if arg in ("--user", "--password", "--filter-users", "--url", "--config",
               "--sessions-dir", "--user-session", "--log-level", "--report-level",
               "--report-screen", "--jobs", "--flows-dir", "--reports-dir",
               "--extensions"):
        return "%s needs a value: %s=VALUE (note the '=', not a space)." % (arg, arg)
    return ("Unknown option %r. Run --help for the full list." % arg)


def _windows_chrome_paths():
    """Where Chrome installs itself on Windows, which is never on PATH.

    The registry key is the authoritative answer (it is what ShellExecute uses);
    the fixed locations cover a machine where it is missing or unreadable, including
    the per-user install that needs no administrator.
    """
    try:
        import winreg
    except ImportError:
        return
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(
                    root,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                value = winreg.QueryValue(key, None)
        except OSError:
            continue
        if value:
            yield value
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            yield os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")


def _chrome_candidates():
    """Everything that might be a Chrome, best first."""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "chrome"):
        path = shutil.which(name)
        if path:
            yield path
    # macOS keeps the binary inside an .app bundle, which is not on PATH.
    if sys.platform == "darwin":
        for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     os.path.expanduser("~/Applications/Google Chrome.app/Contents/"
                                        "MacOS/Google Chrome"),
                     "/Applications/Chromium.app/Contents/MacOS/Chromium"):
            if os.path.exists(path):
                yield path
    if os.name == "nt":
        for path in _windows_chrome_paths():
            if os.path.exists(path):
                yield path


def find_chrome():
    """The browser to launch, or None.

    A candidate that answers ``--version`` wins over one that does not, because
    being on PATH is not the same as being a browser: Ubuntu's `chromium-browser`
    is a 2 KB shim that only redirects to a snap, and on a machine without snapd
    it launches nothing. Preferring a version-answering binary picks the real
    Chrome when both are installed; when nothing answers, the first candidate is
    still returned, so an environment that merely blocks --version behaves as it
    always has and the caller reports the failure (see describe_chrome).
    """
    first = None
    for path in _chrome_candidates():
        if first is None:
            first = path
        if _chrome_version_string(path):
            return path
    return first


def chrome_missing_message():
    """What to tell someone who has no Chrome, in the terms of their platform.

    An installed build is used by people who did not choose to install Python and
    will not read a traceback, so the failure has to name the fix.
    """
    if os.name == "nt":
        how = "Install Google Chrome from https://www.google.com/chrome/"
    elif sys.platform == "darwin":
        how = ("Install Google Chrome from https://www.google.com/chrome/ "
               "and drag it into /Applications")
    else:
        # Not "apt install chromium-browser": on Ubuntu 22.04 that package is a
        # shim that only redirects to a snap, and installing it produces something
        # that looks like a browser on PATH and launches nothing.
        how = ("Download the .deb from https://www.google.com/chrome/ and install "
               "it with:\n"
               "  sudo apt install ./google-chrome-stable_current_amd64.deb\n"
               "(Chromium also works:  sudo snap install chromium)")
    return "Google Chrome was not found on this computer.\n\n%s" % how


def describe_chrome():
    """{path, version, message} for --describe: is there a usable browser?

    A non-empty ``message`` means no, and says how to fix it, so a front-end can
    show the answer without knowing anything about platforms. Note that a path
    with no version is also a "no": that is what Ubuntu's snap-transitional
    `chromium-browser` shim looks like, and launching it opens nothing.
    """
    path = find_chrome()
    if not path:
        return {"path": "", "version": "", "message": chrome_missing_message()}
    version_line = _chrome_version_string(path)
    if not version_line:
        return {"path": path, "version": "",
                "message": "%s exists but does not run - it is most likely Ubuntu's "
                           "snap placeholder rather than a browser.\n\n%s"
                           % (path, chrome_missing_message())}
    return {"path": path, "version": version_line, "message": ""}


def _chrome_version_string(chrome):
    """Chrome's own version banner, or "" when it will not say."""
    try:
        out = subprocess.run([chrome, "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=10,
                             env=runtime_paths.clean_subprocess_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out or "").strip()


# Chrome/webkit timestamps are microseconds since 1601-01-01.
_CHROME_EPOCH_US = 11644473600000000


# --password-store is a Linux-only switch: macOS keys off the login Keychain and
# Windows off DPAPI, so passing it there is meaningless.
_PASSWORD_STORE_ARGS = ["--password-store=basic"] if sys.platform.startswith("linux") else []

# prctl(2) option number for PR_SET_PDEATHSIG (Linux, stable across arches).
_PR_SET_PDEATHSIG = 1

# Execution-overlay (HUD) components accepted by --execution-overlay. Keep in
# sync with engine.overlay.KNOWN_COMPONENTS (defined here too so a plain launch
# never imports the engine just to validate the flag).
_OVERLAY_COMPONENTS = ("tree", "progress", "status", "logs", "highlight", "notifications")

# Same deal for the report flags: mirrors engine.artifacts.ARTIFACTS / SCREEN_MODES
# so --describe can advertise the choices without importing the engine.
_REPORT_ARTIFACTS = ("console", "dom", "result", "screen", "url")
_REPORT_SCREEN_MODES = ("start", "each", "finish")


def _preexec_die_with_parent():
    """Child-side hook: ask the kernel to signal us when our parent launcher dies.

    Runs in the forked child just before exec (Linux only). We start a fresh session
    (setsid) so the whole window tree can still be killpg'd / survive --detach, then
    arm PR_SET_PDEATHSIG so Chrome gets a SIGTERM the moment the launcher process
    goes away - even a SIGKILL, which no Python handler can catch. This is what makes
    VS Code's "Stop" button (which hard-kills the debuggee) close the windows too.
    """
    os.setsid()  # own session/process group (replaces start_new_session=True)
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except (OSError, AttributeError):
        pass  # non-Linux libc or prctl missing: best-effort, skip silently

# Keychain items Chrome/Chromium store their macOS "Safe Storage" secret under.
_MACOS_KEYCHAIN_ITEMS = (("Chrome Safe Storage", "Chrome"),
                         ("Chromium Safe Storage", "Chromium"))


def _macos_safe_storage_key():
    """Derive Chrome's password-store key from the macOS login Keychain.

    On macOS Chrome does not use a fixed secret: it generates a random password once
    per install and keeps it in the login Keychain as "Chrome Safe Storage", then
    stretches it with 1003 PBKDF2 rounds. Reading it the first time pops a Keychain
    permission dialog - choose "Always Allow" so later runs are silent.
    """
    for service, account in _MACOS_KEYCHAIN_ITEMS:
        try:
            done = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", service, "-a", account],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, text=True)
        except (subprocess.CalledProcessError, OSError):
            continue  # item absent (e.g. Chromium vs Chrome) or access denied
        secret = done.stdout.strip()
        if secret:
            return hashlib.pbkdf2_hmac("sha1", secret.encode("utf-8"), b"saltysalt", 1003, 16)
    raise RuntimeError("could not read the Chrome Safe Storage key from the macOS "
                       "Keychain (run Chrome once, then allow the Keychain prompt)")


def _encrypt_password(plaintext):
    """Encrypt a password the way this platform's Chrome password store does (v10).

    Same envelope everywhere - AES-128-CBC, PKCS7 padding, fixed IV, "v10" prefix -
    but the key differs: Linux with --password-store=basic derives it from the fixed
    password "peanuts" in a single PBKDF2 round, while macOS uses the random Keychain
    secret and 1003 rounds. Using the wrong one writes a blob Chrome cannot decrypt.

    Windows is not supported here: Chrome there uses AES-256-GCM under a per-install
    key that is itself DPAPI-encrypted in the profile's Local State, a different
    envelope entirely. seed_password skips the step rather than writing a blob Chrome
    would silently fail to read - the auto-login extension is what actually signs in,
    so only Chrome's saved-passwords list stays empty.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if sys.platform == "darwin":
        key = _macos_safe_storage_key()
    else:
        key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
    iv = b" " * 16
    data = plaintext.encode("utf-8")
    pad = 16 - (len(data) % 16)
    data += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return b"v10" + enc.update(data) + enc.finalize()


def _ensure_login_db(chrome, profile):
    """Return the path to this profile's 'Login Data', creating it if absent.

    We let Chrome build the DB (headless, throwaway) so the schema/version always
    match the installed Chrome, then we inject into it. No-op once it exists.
    """
    db = os.path.join(profile, "Default", "Login Data")
    if os.path.exists(db):
        return db
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--user-data-dir=%s" % profile,
            *_PASSWORD_STORE_ARGS,
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=runtime_paths.clean_subprocess_env(),
    )
    for _ in range(40):  # up to ~10s for Chrome to create the file
        if os.path.exists(db):
            break
        time.sleep(0.25)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return db


def seed_password(chrome, profile, origin, login, password):
    """Save exactly one credential in this profile's Chrome password manager.

    Any existing entry for the same site is removed first, so the isolated window's
    password manager holds only this user's login/password. The value is encrypted with
    this platform's Chrome key (see _encrypt_password) so Chrome can decrypt it.

    A no-op on Windows, where that key is not reproducible from outside Chrome.
    """
    if os.name == "nt":
        log.info("Windows: skipping the saved-password step (Chrome's password "
                 "store is not writable from outside). Auto-login is unaffected.")
        return
    db = _ensure_login_db(chrome, profile)
    if not os.path.exists(db):
        log.warning("Could not create Login Data; skipping password save.")
        return
    realm = origin + "/"
    login_url = origin + LOGIN_PATHS[2]   # the app's login page
    now = int(time.time() * 1_000_000) + _CHROME_EPOCH_US
    con = sqlite3.connect(db)
    try:
        # one credential per site: drop any prior entry for this origin first
        con.execute("DELETE FROM logins WHERE signon_realm = ?", (realm,))
        con.execute(
            "INSERT INTO logins ("
            "origin_url, action_url, username_element, username_value,"
            "password_element, password_value, submit_element, signon_realm,"
            "date_created, blacklisted_by_user, scheme, date_password_modified"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                login_url, login_url, "login", login,
                "password", _encrypt_password(password), "", realm,
                now, 0, 0, now,
            ),
        )
        con.commit()
    finally:
        con.close()


def set_profile_name(profile, name):
    """Name the Chrome profile so the label shows in the top-right profile pill.

    Persists across page navigation (unlike the title bar, which follows the page).
    Merged into the existing Preferences so a logged-in session is not wiped.
    Only takes effect on the next launch, so run this while the window is closed.
    """
    default = os.path.join(profile, "Default")
    os.makedirs(default, exist_ok=True)
    prefs_path = os.path.join(default, "Preferences")
    prefs = {}
    if os.path.exists(prefs_path):
        try:
            with open(prefs_path, encoding="utf-8") as fh:
                prefs = json.load(fh)
        except (ValueError, OSError):
            prefs = {}
    prefs.setdefault("profile", {})["name"] = name
    # Force Chrome to "Continue where you left off" so transient session cookies are not
    # deleted on exit. The tabs it would otherwise restore are wiped by clear_previous_tabs,
    # so this keeps the login without piling up tabs.
    if "session" not in prefs:
        prefs["session"] = {}
    prefs["session"]["restore_on_startup"] = 1
    # Chrome nags about an "unclean shutdown" and offers to restore tabs when it was killed
    # (which is how close_all/CTRL+C stops it). Mark the last exit clean to suppress that.
    prefs["profile"]["exit_type"] = "Normal"
    prefs["profile"]["exited_cleanly"] = True
    with open(prefs_path, "w", encoding="utf-8") as fh:
        json.dump(prefs, fh)


def clear_previous_tabs(profile):
    """Remove Chrome's saved tab/session state so the next launch opens a single tab.

    restore_on_startup=1 keeps session cookies alive but would also reopen every tab from
    last time. The tab list lives in these Session/Tabs files; cookies live in the separate
    Cookies DB, so deleting these gives one clean tab while leaving the login untouched.
    Run this only while the window is closed.
    """
    default = os.path.join(profile, "Default")
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        try:
            os.remove(os.path.join(default, name))
        except OSError:
            pass  # missing (fresh profile) or locked; nothing to clear
    # Newer Chrome keeps rolling snapshots under Default/Sessions/ instead.
    sessions_dir = os.path.join(default, "Sessions")
    if os.path.isdir(sessions_dir):
        for fname in os.listdir(sessions_dir):
            if fname.startswith(("Session_", "Tabs_")):
                try:
                    os.remove(os.path.join(sessions_dir, fname))
                except OSError:
                    pass


def clear_devtools_port(profile):
    """Drop the previous run's DevToolsActivePort before relaunching this profile.

    Chrome removes the file on a clean exit, but a window killed outright (SIGKILL
    on the close-timeout path, or PR_SET_PDEATHSIG when the launcher dies) leaves it
    behind holding a dead port. The engine's wait_for_devtools polls this file and
    would return that stale port on its FIRST poll - attaching to nothing, or worse,
    to whatever process the OS has since handed that port to, which can be another
    session's Chrome. Run this only while the window is closed.
    """
    try:
        os.remove(os.path.join(profile, "DevToolsActivePort"))
    except OSError:
        pass  # fresh profile, or already cleaned up by a clean exit


# Default login-form selectors, used when nothing overrides them. Names are the
# common convention; an app that uses different ones (username/email, an id instead
# of a name) overrides them. They live here rather than in the extension source so
# retargeting is a config change, not a JavaScript edit - the source reads them from
# the generated config.js.
DEFAULT_LOGIN_SELECTORS = {
    "login": 'input[name="login"]',
    "password": 'input[name="password"]',
    "submit": 'button[type="submit"]',
}

# The auto-login extension's SOURCE. Underscore-prefixed so local_extensions()
# skips it: it is machinery, not something to install with --extensions (it is
# useless without the per-profile credentials written alongside it).
AUTOLOGIN_SRC = os.path.join(EXTENSIONS_DIR, "_autologin")


def write_autologin_extension(ext_dir, origin, login, password, key_b64=None,
                              selectors=None, src_dir=None):
    """Install the auto-login extension into ext_dir, with this user's credentials.

    The behaviour lives in extensions/_autologin/ as ordinary, editable, lintable
    files. Only the per-profile parts are generated here:

      config.js  - credentials + selectors, loaded BEFORE content.js in the same
                   content_scripts entry, so they share one isolated world. Secrets
                   stay out of the checked-in source and exist only inside the profile.
      manifest   - the source manifest with this user's name, origin match and key.

    key_b64 is the base64 DER public key; embedding it gives the extension a stable,
    key-derived id (see install_autologin_extension) so Chrome can load it from the
    profile without --load-extension, which recent Chrome (137+) blocks.
    """
    src = src_dir or AUTOLOGIN_SRC
    if os.path.isdir(ext_dir):
        shutil.rmtree(ext_dir, ignore_errors=True)   # refresh, so source edits apply
    shutil.copytree(src, ext_dir)

    with open(os.path.join(ext_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    manifest["name"] = "Auto-login (%s)" % login
    for entry in manifest.get("content_scripts", []):
        entry["matches"] = [origin + "/*"]
    if key_b64:
        manifest["key"] = key_b64
    with open(os.path.join(ext_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    config = {
        "login": login,
        "password": password,
        "selectors": dict(DEFAULT_LOGIN_SELECTORS, **(selectors or {})),
    }
    with open(os.path.join(ext_dir, "config.js"), "w", encoding="utf-8") as fh:
        # json.dumps handles every escaping question a password can raise.
        fh.write("var AUTOLOGIN = %s;\n" % json.dumps(config))


def _extension_id_from_key(pub_der):
    """Chrome's extension id: first 32 hex chars of SHA256(DER public key), mapped
    onto a-p (each hex nibble 0-f -> a-p). This is the same id Chrome derives from
    the manifest "key", so writing that key gives us a stable, predictable id.
    """
    digest = hashlib.sha256(pub_der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def _profile_extension_key(ext_root, key_file_name):
    """Return a profile-local base64 DER public key, generating+caching it once.

    The key only exists to give an extension a stable id (no CRX is ever signed), so
    we keep just the public half in key_file_name and reuse it on later runs - that
    way the profile keeps one folder per extension instead of piling up a new id each
    launch. A distinct key_file_name per extension keeps their ids from colliding.
    """
    key_file = os.path.join(ext_root, key_file_name)
    if os.path.exists(key_file):
        with open(key_file, encoding="utf-8") as fh:
            return fh.read().strip()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    b64 = base64.b64encode(der).decode("ascii")
    with open(key_file, "w", encoding="utf-8") as fh:
        fh.write(b64)
    return b64


def _merge_extension_pref(profile, ext_id, entry):
    """Merge one extensions.settings entry into the profile's Preferences file.

    Chrome ingests it on startup and re-saves it into its tamper-protected Secure
    Preferences, so the extension sticks. Re-planting the same entry every launch is
    idempotent, so callers may run each launch (this merges into the Preferences file
    set_profile_name already wrote, keeping the rest).
    """
    prefs_path = os.path.join(profile, "Default", "Preferences")
    prefs = {}
    if os.path.exists(prefs_path):
        try:
            with open(prefs_path, encoding="utf-8") as fh:
                prefs = json.load(fh)
        except (ValueError, OSError):
            prefs = {}
    prefs.setdefault("extensions", {}).setdefault("settings", {})[ext_id] = entry
    with open(prefs_path, "w", encoding="utf-8") as fh:
        json.dump(prefs, fh)


def install_autologin_extension(profile, origin, login, password):
    """Install the auto-login extension straight into the profile, no --load-extension.

    Recent Chrome (137+) refuses to load unpacked extensions from the command line
    ("Installation is not enabled"), so instead we register it the way a normally
    installed extension is: the files under Default/Extensions/<id>/<ver>/ plus an
    entry in the profile's Preferences. Chrome ingests that on startup and re-saves
    it into its tamper-protected Secure Preferences, so the extension persists and
    loads on every later launch - the same "prepare it once in the profile" approach
    used for the seeded password (see seed_password).

    Re-planting the same entry every run is idempotent, so this is safe to call each
    launch (like set_profile_name, which it merges into the same Preferences file).
    """
    ext_root = os.path.join(profile, "Default", "Extensions")
    os.makedirs(ext_root, exist_ok=True)
    key_b64 = _profile_extension_key(ext_root, ".autologin_key")
    ext_id = _extension_id_from_key(base64.b64decode(key_b64))
    version = "1.0"
    version_dir = os.path.join(ext_root, ext_id, version + "_0")
    write_autologin_extension(version_dir, origin, login, password, key_b64=key_b64)

    # The extension only touches its own site, so it just needs that host permission.
    host = [origin + "/*"]
    perms = {"api": [], "explicit_host": [], "manifest_permissions": [], "scriptable_host": host}
    entry = {
        "active_permissions": perms,
        "granted_permissions": perms,
        # location 1 = INTERNAL: Chrome treats it as a regular installed extension,
        # not an unpacked/command-line one, so the 137+ side-load block never applies.
        "location": 1,
        "state": 1,  # enabled
        "from_webstore": False,
        "was_installed_by_default": False,
        "was_installed_by_oem": False,
        "path": "%s/%s_0" % (ext_id, version),
        "manifest": json.load(open(os.path.join(version_dir, "manifest.json"), encoding="utf-8")),
    }
    _merge_extension_pref(profile, ext_id, entry)
    return ext_id


# Chrome Web Store extensions that can be installed into every profile, by name.
# Nothing here is installed unless --extensions asks for it: an automated profile
# should carry what the run needs and nothing else, and each entry costs a fetch
# from Google on first use.
#
# A raw 32-character Web Store id is accepted too, so a fork can install any
# extension without editing this table.
#
# Entries are just convenience aliases for store ids; see extensions/README.md for
# what ships in this checkout and under whose licence.
KNOWN_EXTENSIONS = {
    "odoo_debug": "hmdmhilocobgohohpdpolmibjklfgkbi",
}

# Web Store ids are 32 chars from a-p (a base-16 alphabet shifted into letters).
_EXT_ID_RE = re.compile(r"^[a-p]{32}$")


# One resolved --extensions entry. kind is "local" (value = source directory) or
# "store" (value = a Chrome Web Store id to download).
Extension = collections.namedtuple("Extension", "name kind value")


def local_extensions(extensions_dir=None):
    """Unpacked extensions vendored in the project: {name: source directory}.

    A directory under extensions/ counts only if it has a manifest.json, so notes,
    README files and scratch folders sitting alongside are ignored.
    """
    root = extensions_dir or EXTENSIONS_DIR
    found = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for name in names:
        if name.startswith("_"):
            continue        # machinery (e.g. _autologin), not user-installable
        path = os.path.join(root, name)
        if os.path.isfile(os.path.join(path, "manifest.json")):
            found[name] = path
    return found


def validate_local_extension(path):
    """(ok, reason) for an unpacked extension directory.

    Cheap, static checks only - enough to catch a truncated download, a stray
    directory or a manifest Chrome will refuse, so one broken extension is skipped
    with a warning instead of taking the whole launch down.
    """
    manifest_path = os.path.join(path, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except ValueError as exc:
        return False, "manifest.json is not valid JSON (%s)" % exc
    except OSError as exc:
        return False, "manifest.json is unreadable (%s)" % exc
    if not isinstance(manifest, dict):
        return False, "manifest.json is not a JSON object"
    missing = [k for k in ("manifest_version", "name", "version") if k not in manifest]
    if missing:
        return False, "manifest.json is missing %s" % ", ".join(missing)
    if manifest.get("manifest_version") not in (2, 3):
        return False, ("manifest_version %r is not supported by Chrome"
                       % manifest.get("manifest_version"))
    for entry in manifest.get("content_scripts") or []:
        for js in entry.get("js") or []:
            if not os.path.isfile(os.path.join(path, js)):
                return False, "content script %r is missing" % js
    return True, ""


def default_extensions(extensions_dir=None):
    """Every usable unpacked extension in the project - what a plain launch installs.

    Broken ones are dropped with a warning rather than failing the launch: a bad
    extension should cost you that extension, not your windows.
    """
    chosen = []
    for name, path in sorted(local_extensions(extensions_dir).items()):
        ok, reason = validate_local_extension(path)
        if ok:
            chosen.append(Extension(name, "local", path))
        else:
            log.warning("Skipping extension %s: %s (%s)", name, reason, path)
    return chosen


def format_extensions(extensions_dir=None):
    """The --extensions=list output: what is known, and how to use anything else."""
    local = local_extensions(extensions_dir)
    lines = []
    if local:
        lines.append("Unpacked extensions in this project (installed with no network):")
        width = max(len(n) for n in local)
        for name in sorted(local):
            lines.append("  %-*s  %s" % (width, name, local[name]))
        lines.append("")
    lines.append("Extensions fetched from the Chrome Web Store by name:")
    width = max((len(n) for n in KNOWN_EXTENSIONS), default=0)
    for name in sorted(KNOWN_EXTENSIONS):
        marker = "  (overridden by the local copy above)" if name in local else ""
        lines.append("  %-*s  %s%s" % (width, name, KNOWN_EXTENSIONS[name], marker))
    if not KNOWN_EXTENSIONS:
        lines.append("  (none)")
    lines += [
        "",
        "Without --extensions, EVERY usable extension above is installed. One that is",
        "broken or unsupported is skipped with a warning; the launch continues.",
        "  --extensions=none        install nothing",
        "  --extensions=all         the default set, stated explicitly",
        "  --extensions=a,b         exactly these",
        "",
        "To add your own: drop an unpacked extension in extensions/<name>/ (it needs a",
        "manifest.json). Nothing is downloaded, and the source is re-copied into each",
        "profile on every launch, so edits take effect.",
        "",
        "",
        "Any other extension: pass its 32-character Chrome Web Store id directly -",
        "no code change needed. The id is the last part of the store URL:",
        "  https://chromewebstore.google.com/detail/<slug>/hmdmhilocobgohohpdpolmibjklfgkbi",
        "                                                 ^--------- this ---------^",
        "  --extensions=hmdmhilocobgohohpdpolmibjklfgkbi",
        "",
        "You can also give an id a name inline, which is what the cache file and the",
        "log lines are called:",
        "  --extensions=react_devtools=fmkadmapgofadopljbjfkapdkoienihi",
        "",
        "To add a name permanently, put it in KNOWN_EXTENSIONS in session_launcher.py.",
    ]
    return "\n".join(lines)


def resolve_extension(entry, extensions_dir=None):
    """Map one --extensions entry to (name, webstore_id), or exit with the known list.

    Accepts, in order: "name=id" (name it inline), a friendly name from
    KNOWN_EXTENSIONS, or a raw Web Store id. The inline and raw forms mean a fork
    can install anything without editing this file.
    """
    key = entry.strip()
    if "=" in key:                       # name=id: self-documenting, no registry edit
        name, _sep, ext_id = key.partition("=")
        name, ext_id = name.strip(), ext_id.strip()
        if not name or not _EXT_ID_RE.match(ext_id):
            sys.exit("--extensions: %r is not name=<32-char store id>.\n%s"
                     % (entry, format_extensions(extensions_dir)))
        return Extension(name, "store", ext_id)
    local = local_extensions(extensions_dir)
    if key in local:
        # A local copy wins over the same name in the store table: that is how you
        # pin or patch a store extension without renaming every command that uses it.
        return Extension(key, "local", local[key])
    if key in KNOWN_EXTENSIONS:
        return Extension(key, "store", KNOWN_EXTENSIONS[key])
    if _EXT_ID_RE.match(key):
        return Extension(key, "store", key)   # raw id: cache file named after the id
    sys.exit("--extensions: unknown extension %r.\n%s"
             % (entry, format_extensions(extensions_dir)))


def _chrome_prodversion(chrome):
    """Best-effort Chrome major.minor for the CRX update query; falls back high."""
    try:
        out = subprocess.run([chrome, "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, timeout=10,
                             env=runtime_paths.clean_subprocess_env()).stdout
        match = re.search(r"(\d+\.\d+)", out or "")
        if match:
            return match.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return "150.0"


def download_crx(dest_path, ext_id, prodversion):
    """Download an extension's CRX from Chrome's update service to dest_path (cached).

    Skips the download when the file already exists, so the network is hit once per
    sessions dir. Raises on failure so the caller can warn and carry on without it.
    """
    if os.path.exists(dest_path):
        return dest_path
    from urllib.request import urlopen, Request
    url = ("https://clients2.google.com/service/update2/crx?response=redirect"
           "&acceptformat=crx2,crx3&prodversion=%s"
           "&x=id%%3D%s%%26installsource%%3Dondemand%%26uc" % (prodversion, ext_id))
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
    if data[:4] != b"Cr24":
        raise ValueError("update service did not return a CRX (%d bytes)" % len(data))
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp = dest_path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, dest_path)
    return dest_path


def _crx_zip_bytes(crx):
    """Strip the CRX2/CRX3 header, returning the embedded ZIP bytes."""
    if crx[:4] != b"Cr24":
        raise ValueError("not a CRX file")
    version = int.from_bytes(crx[4:8], "little")
    if version == 3:
        header_len = int.from_bytes(crx[8:12], "little")
        return crx[12 + header_len:]
    if version == 2:
        pk_len = int.from_bytes(crx[8:12], "little")
        sig_len = int.from_bytes(crx[12:16], "little")
        return crx[16 + pk_len + sig_len:]
    raise ValueError("unsupported CRX version %d" % version)


def install_crx_extension(profile, crx_path, origin, key_file_name):
    """Install a downloaded CRX into the profile as an enabled, silent local extension.

    Chrome will not silently enable an extension that looks like it came from the Web
    Store or asks for broad host access, so before registering it we rewrite three
    things in its manifest: (1) re-key it under our own key -> a plain non-store id
    Chrome won't flag as a remote/webstore install, (2) drop its "update_url" (same
    reason, and it stops Chrome auto-updating it back to the store build), and
    (3) narrow any "<all_urls>" content-script / web-accessible matches to this
    session's origin, so it needs only that one host permission. Granted permissions
    are taken from the rewritten manifest, so there is no pending permission prompt.
    Uses the same profile-Preferences mechanism as install_autologin_extension.
    """
    import io
    import zipfile
    ext_root = os.path.join(profile, "Default", "Extensions")
    os.makedirs(ext_root, exist_ok=True)
    key_b64 = _profile_extension_key(ext_root, key_file_name)
    ext_id = _extension_id_from_key(base64.b64decode(key_b64))
    with open(crx_path, "rb") as fh:
        zf = zipfile.ZipFile(io.BytesIO(_crx_zip_bytes(fh.read())))
    manifest = json.loads(zf.read("manifest.json"))
    version = manifest.get("version", "0")
    version_dir = os.path.join(ext_root, ext_id, version + "_0")
    if not os.path.isdir(version_dir):  # unzip once; manifest is rewritten every run
        os.makedirs(version_dir)
        zf.extractall(version_dir)

    _register_extension(profile, ext_id, version_dir, version, manifest, key_b64, origin)
    return ext_id


def _register_extension(profile, ext_id, version_dir, version, manifest, key_b64, origin):
    """Re-key a third-party manifest, narrow it to this origin, and enable it.

    Shared by the CRX and local-source installers. Chrome will not silently enable
    an extension that looks like a Web Store install or asks for broad host access,
    so: re-key it under our own key (a plain non-store id), drop "update_url" (stops
    Chrome updating it back to the store build), and narrow any "<all_urls>" content
    script / web-accessible match down to this session's origin. Granted permissions
    are taken from the rewritten manifest, so nothing is left pending a prompt.
    """
    host = origin + "/*"
    manifest["key"] = key_b64
    manifest.pop("update_url", None)
    has_content_script = bool(manifest.get("content_scripts"))
    for cs in manifest.get("content_scripts", []):
        cs["matches"] = [host]
    for war in manifest.get("web_accessible_resources", []):
        if isinstance(war, dict) and "matches" in war:
            war["matches"] = [host]
    with open(os.path.join(version_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    perms = {
        "api": manifest.get("permissions", []),
        "explicit_host": [host] if manifest.get("host_permissions") else [],
        "manifest_permissions": [],
        "scriptable_host": [host] if has_content_script else [],
    }
    entry = {
        "active_permissions": perms,
        "granted_permissions": perms,
        "location": 1,  # INTERNAL (see install_autologin_extension)
        "state": 1,
        "from_webstore": False,
        "was_installed_by_default": False,
        "was_installed_by_oem": False,
        "path": "%s/%s_0" % (ext_id, version),
        "manifest": manifest,
    }
    _merge_extension_pref(profile, ext_id, entry)


def install_local_extension(profile, src_dir, origin, key_file_name):
    """Install an UNPACKED extension from a source directory in this project.

    The local counterpart of install_crx_extension: no Web Store, no network, and
    the source is yours to edit. Files are re-copied on every launch so editing the
    source and relaunching picks the change up, rather than silently reusing a stale
    copy planted under a version that did not change.
    """
    with open(os.path.join(src_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    ext_root = os.path.join(profile, "Default", "Extensions")
    os.makedirs(ext_root, exist_ok=True)
    key_b64 = _profile_extension_key(ext_root, key_file_name)
    ext_id = _extension_id_from_key(base64.b64decode(key_b64))
    version = str(manifest.get("version", "0"))
    version_dir = os.path.join(ext_root, ext_id, version + "_0")
    if os.path.isdir(version_dir):
        shutil.rmtree(version_dir, ignore_errors=True)
    shutil.copytree(src_dir, version_dir)
    _register_extension(profile, ext_id, version_dir, version, manifest, key_b64, origin)
    return ext_id


def close_all(procs):
    """Close every launched window gracefully so the login session is flushed to disk.

    SIGTERM goes to the browser process only (proc.pid), which lets Chrome run its normal
    shutdown: write session cookies to the Cookies DB and close its own child processes.
    Signalling the whole process group at once (killpg) instead looks like a crash to Chrome
    and it skips that flush, which loses the login. Only a hung browser is force-killed, and
    only then is the group nuked to sweep up any orphaned helpers.
    """
    for cls, proc in procs:
        if proc.poll() is not None:
            continue  # already closed (e.g. user shut this window manually)
        log.info("Closing %s...", cls)
        _emit("window.closing", cls=cls, pid=proc.pid)
        try:
            proc.terminate()  # SIGTERM to the browser -> graceful Chrome shutdown
        except ProcessLookupError:
            pass
    # Give Chrome time to persist cookies before force-killing anything still alive.
    for cls, proc in procs:
        try:
            proc.wait(timeout=15)
            _emit("window.exited", cls=cls, pid=proc.pid, returncode=proc.returncode)
        except subprocess.TimeoutExpired:
            _emit("window.killed", cls=cls, pid=proc.pid)
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()


def keep_open_until_closed(procs):
    """Block until every window is closed manually or a shutdown signal arrives.

    The VS Code "Stop" button (and any `kill` without CTRL+C) sends SIGTERM, not
    SIGINT, so the KeyboardInterrupt path below would never run and the windows
    would be orphaned (they launch in their own session, detached from us). Raise
    KeyboardInterrupt on SIGTERM too, so Stop cleans up exactly like CTRL+C does.
    """
    def _on_sigterm(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_sigterm)

    log.info("Windows are tracked in this session. Press CTRL+C (or Stop) to close all of them.")
    try:
        # poll() is already called every half-second here, so noticing a window
        # that went away costs nothing extra beyond remembering which ones we
        # have reported.
        reported = set()
        while any(proc.poll() is None for _cls, proc in procs):
            for cls, proc in procs:
                code = proc.poll()
                if code is not None and proc.pid not in reported:
                    reported.add(proc.pid)
                    _emit("window.exited", cls=cls, pid=proc.pid, returncode=code)
            time.sleep(0.5)
        for cls, proc in procs:
            if proc.pid not in reported:
                _emit("window.exited", cls=cls, pid=proc.pid, returncode=proc.poll())
        log.info("All windows were closed manually.")
    except KeyboardInterrupt:
        log.info("Shutdown signal received - closing all windows...")
        close_all(procs)
        log.info("Done.")


def _print_help():
    """Print every command-line parameter with a one-line description and exit 0."""
    prog = os.path.basename(sys.argv[0])
    print("""\
Usage: %s [OPTIONS] (--env=NAME | --url=URL | <URL>)

Launch one Chrome window per configured user, each auto-logged-in via a
per-profile extension. With --run-tests it also drives flows and writes reports.

Options:
  --env=NAME                Launch one environment: matches the config's "env"
                            field by short name (--env=dev), and supplies that
                            environment's URL, so --url becomes optional.
                            Default: every environment.
  --url=URL                 Login URL (or pass it as a positional argument);
                            overrides the URL --env would supply.
  --user-session=PREFIX     Folder-name prefix for this run's profile dirs (keeps
                            separate sessions isolated); overrides the env.
  --sessions-dir=DIR        Where to store profiles + generated extensions
                            (default: user_sessions/ next to the script).
  --config=FILE             Path to the users JSON config (default: users.json).
  --init-users-json         Write a starter users.json (at --config's path) and
                            exit; never overwrites an existing file.
  --filter-users=LIST       Launch only these logins, comma-separated
                            (default: all).
  --user=LOGIN              Launch a single user instead of the config list; its
                            password comes from the config (or its environment)
                            unless --password is given.
  --password=PASS           Password to use, overriding the config's. With --user
                            it applies to that user; alone, to every selected one.
  --detach                  Fire-and-forget: leave windows running after exit.
  --extensions=LIST         Which extensions to install into every profile.
                            Default (no flag): every usable one in extensions/.
                            LIST is comma-separated names, raw 32-char store ids
                            or name=id; "none" installs nothing, "all" is the
                            default set, "list" prints what is available.
  --log-level=LEVEL         DEBUG/INFO/WARNING/ERROR (default: INFO; also via
                            the OPEN_USERS_LOG_LEVEL env var).
  --events=-|FILE           Emit a structured JSONL event stream for another
                            program (the GUI) to follow: windows launched, CDP
                            attached, every step, artifacts written, run summary.
                            "-" is stdout - log records go to stderr, so the two
                            never mix; anything else is a file to append to.
  --version, -V             Print the version and exit.
  --help, -h                Show this help and exit.

Editing scenarios (answer with JSON on stdout, then exit):
  --flow-show=ID            One scenario as JSON: its text, its parsed steps,
                            whether it can be edited, and anything it references
                            that does not resolve here.
  --flow-save=ID --from=F   Write a scenario from the JSON document in file F -
                            either {"yaml": "..."} or {"meta": {...},
                            "steps": [...]}. Validated by compiling it first;
                            nothing is written unless it compiles.
  --flow-delete=ID          Delete a scenario. Refuses the ones that ship with
                            the application - duplicate those instead.
  --flow-import=FILE        Copy a scenario file in, validating it first.
  --selectors-show          The named-target map as JSON: every name, what it
                            resolves to, and whether it is yours or the app's.
  --selectors-save --from=F Replace your selectors.yaml from the JSON document
                            in file F ({"yaml": "..."}). Names of your own
                            override the ones that ship with the application.

Recording:
  --recorder                Open the windows with the Scenario Recorder shown in
                            each of them. Nothing is recorded until you ask:
                            Capture Step (or F2) picks one element and one action
                            at a time, and Finish writes the scenario.
                            --recorder=ID continues an existing scenario or names
                            a new one; the default is a timestamp.

Flow execution (require --run-tests):
  --run-tests=LIST          Attach over CDP, run scenarios, write reports, then
                            exit (0 = all passed). Values: all, config (each user
                            runs its own "tests" field from the config), or a
                            comma-separated list of ids / tag:NAME.
  --execution-overlay=LIST  In-page execution HUD; components tree,progress,
                            status,logs,highlight (or all). highlight flashes a
                            box on each clicked/typed/pressed element.
  --flows-dir=DIR           Where scenarios/blocks/selectors.yaml live (default:
                            flows/ next to the script). Lets the flows live in
                            their own repository.
  --reports-dir=DIR         Where run artifacts are written (default: reports/).
  --jobs=N|all              Drive N windows at once (default 1 = one after
                            another); extras queue for a free slot. Scenarios
                            within a window always run in order.
  --close-after             Close the browser windows once the run finishes;
                            without it they stay open for inspection.

Reports (require --run-tests):
  --report-level=LIST       What artifacts to generate: console,dom,result,
                            screen,url (default: result on success, the full
                            bundle on failure).
  --report-always           Produce a full report on success too, not only
                            on failure.
  --report-screen=LIST      When to capture screenshots: start,each,finish
                            (only when 'screen' is in --report-level).

Example:
  %s --user=admin --password=admin --url=http://localhost:8069 \\
    --run-tests=my_scenario --report-level=result,screen --report-screen=start,finish\
""" % (prog, prog))
    sys.exit(0)


def main():
    # An installed build has no checkout to fall back on, so the user's directories
    # and a starter users.json have to exist before anything reads them. A no-op in
    # a source checkout - see runtime_paths.ensure_user_data_root.
    runtime_paths.ensure_user_data_root()
    detach = False
    session_prefix = ""
    url = ""
    sessions_dir = DEFAULT_SESSIONS_DIR
    config_path = DEFAULT_CONFIG
    cli_user = None
    cli_password = None
    init_config = False
    describe_json = False # --describe: print the JSON inventory and exit
    flow_command = None   # ("show"|"save"|"delete"|"import", id or path); JSON, then exit
    flow_source = None    # --from=FILE: the JSON document --flow-save writes
    users_filter = None   # None = launch every user in the config
    env_name = None       # --env=NAME; None = every environment
    recorder = None       # --recorder: record a scenario from a live window
    run_tests = None      # None = just launch; "all" or [ids] = run flows then exit
    jobs = 1              # --jobs: windows driven at once (1 = one after another)
    flows_dir = None      # --flows-dir: where scenarios live (None = the engine default)
    reports_dir = None    # --reports-dir: where run artifacts are written
    jobs_given = False
    overlay_components = None  # None = no HUD; else list of --execution-overlay components
    close_after = False   # with --run-tests: close windows after the run (default: keep open)
    report_level = None   # raw --report-level value (None = flag absent -> default artifacts)
    report_always = False # --report-always: produce a full report on success too
    report_screen = None  # raw --report-screen value (None = flag absent -> default capture)
    extensions = None     # --extensions: None = the default set (every local one)
    extensions_given = False
    excluded_extensions = set()   # from the deprecated --no-odoo-debug
    legacy_odoo_flag = None   # --odoo-debug / --no-odoo-debug, reported once after parsing
    events_target = None  # --events: "-" (stdout) or a file path; None = off
    log_level = os.environ.get("OPEN_USERS_LOG_LEVEL", "INFO")
    bad_option = None     # unusable option we saw, reported once argv is fully parsed
    positional = []
    for arg in sys.argv[1:]:
        if arg in ("--help", "-h"):
            _print_help()   # prints all parameters and exits 0
        elif arg in ("--version", "-V"):
            print("chrome-multi-session %s" % version())
            sys.exit(0)
        elif arg == "--detach":
            detach = True
        elif arg == "--close-after":
            # Flow-execution mode only: after the run finishes (report + screenshots
            # written), close every window automatically. Without it the windows stay
            # open for inspection. Ignored outside --run-tests (checked after the loop).
            close_after = True
        elif arg.startswith("--extensions="):
            # Comma-separated friendly names (see KNOWN_EXTENSIONS) or raw Web Store
            # ids. Nothing is installed by default.
            # Not parse_filter_list: that flag's "empty means all" wording is wrong
            # here - omitting --extensions installs nothing, and there is no "all".
            names = [n.strip() for n in arg.split("=", 1)[1].split(",") if n.strip()]
            if names == ["list"]:
                print(format_extensions())
                sys.exit(0)
            if names == ["none"]:
                extensions = []          # explicit: install nothing
                extensions_given = True
                continue
            if names == ["all"]:
                extensions = None        # explicit: the default set (every local one)
                extensions_given = False
                continue
            extensions_given = True
            if not names:
                sys.exit("--extensions= is empty. Omit the flag to install nothing, or "
                         "name what you want, e.g. --extensions=odoo_debug.")
            if extensions is None:
                extensions = []
            for name in names:
                ext = resolve_extension(name)
                if ext.name not in [e.name for e in extensions]:
                    extensions.append(ext)
        elif arg == "--odoo-debug":
            # Deprecated: no extension is special-cased any more.
            legacy_odoo_flag = arg
            excluded_extensions.discard("odoo_debug")
        elif arg == "--no-odoo-debug":
            # Deprecated: now expressed as an exclusion from the default set.
            legacy_odoo_flag = arg
            excluded_extensions.add("odoo_debug")
        elif arg.startswith("--events="):
            # Structured JSONL event stream for a GUI or any other program
            # driving the launcher: "-" is stdout (logging goes to stderr, so
            # the two never mix), anything else is a file to append to.
            events_target = arg.split("=", 1)[1].strip()
            if not events_target:
                sys.exit("--events= is empty. Use --events=- for stdout, or "
                         "--events=PATH to append to a file.")
        elif arg.startswith("--log-level="):
            log_level = arg.split("=", 1)[1].strip()
        elif arg == "--init-users-json":
            init_config = True
        elif arg == "--describe":
            # Machine-readable inventory (environments, users, scenarios,
            # extensions) for a front-end. Handled after the parse loop so
            # --config/--flows-dir can appear in any order.
            describe_json = True
        elif arg.startswith("--flow-show="):
            # The scenario editing commands. All four answer with JSON and exit,
            # like --describe, and all are handled after the parse loop so
            # --flows-dir can appear on either side of them.
            flow_command = ("show", arg.split("=", 1)[1].strip())
        elif arg.startswith("--flow-save="):
            flow_command = ("save", arg.split("=", 1)[1].strip())
        elif arg.startswith("--flow-delete="):
            flow_command = ("delete", arg.split("=", 1)[1].strip())
        elif arg.startswith("--flow-import="):
            flow_command = ("import", os.path.abspath(os.path.expanduser(
                arg.split("=", 1)[1].strip())))
        elif arg == "--recorder" or arg.startswith("--recorder="):
            # Recording mode: open the windows as usual, but with a debug port
            # and the recorder shown in each of them. Nothing is recorded until
            # Capture Step is pressed - showing it is not capturing.
            recorder = arg.split("=", 1)[1].strip() if "=" in arg else True
        elif arg == "--selectors-show":
            flow_command = ("selectors-show", "")
        elif arg == "--selectors-save":
            flow_command = ("selectors-save", "")
        elif arg.startswith("--from="):
            # The document --flow-save writes, as JSON in a file. A file rather
            # than stdin so the command can be run and debugged from a shell.
            flow_source = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1].strip()))
        elif arg.startswith("--user-session="):
            session_prefix = arg.split("=", 1)[1].strip()
        elif arg.startswith("--url="):
            url = arg.split("=", 1)[1].strip()
        elif arg.startswith("--sessions-dir="):
            sessions_dir = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1].strip()))
        elif arg.startswith("--config="):
            config_path = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1].strip()))
        elif arg.startswith("--flows-dir="):
            # Lets the scenarios live in their own repo, separate from the engine.
            flows_dir = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1].strip()))
        elif arg.startswith("--reports-dir="):
            reports_dir = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1].strip()))
        elif arg.startswith("--filter-users="):
            # --filter-users=all keeps everyone; otherwise a comma-separated list of
            # logins to launch from the config. An empty value is an error.
            users_filter = parse_filter_list("--filter-users", arg.split("=", 1)[1])
        elif arg.startswith("--env="):
            env_name = arg.split("=", 1)[1].strip()
        elif arg == "--env" or arg.startswith("--filter-prefix"):
            # --filter-prefix was replaced by --env. Deferred until after the loop
            # so --config= can appear in any order (the error lists the envs).
            bad_option = arg
        elif arg.startswith("--run-tests="):
            val = arg.split("=", 1)[1].strip()
            # --run-tests=all runs every (non-template) scenario in flows/scenarios;
            # --run-tests=config gives each user its own "tests" from the config, so
            # one launch covers several roles without replaying every scenario against
            # every window; otherwise a comma-separated list of scenario ids. Presence
            # of this flag switches the launcher into flow-execution mode: after
            # launching, attach to each window over CDP, run, write reports, and exit.
            if not val or val.lower() == "all":
                run_tests = "all"
            elif val.lower() == "config":
                run_tests = "config"
            else:
                run_tests = [s.strip() for s in val.split(",") if s.strip()]
        elif arg.startswith("--jobs="):
            # How many WINDOWS are driven at once. Scenarios inside a window always
            # run one after another; this only removes the wait between windows.
            val = arg.split("=", 1)[1].strip().lower()
            jobs_given = True
            if val == "all":
                jobs = "all"
            elif val.isascii() and val.isdigit() and int(val) >= 1:
                jobs = int(val)
            else:
                sys.exit("--jobs: expected a positive number or 'all', got %r." % val)
        elif arg == "--execution-overlay" or arg.startswith("--execution-overlay="):
            # Enable the in-page execution HUD. Value is a comma-separated list of
            # components (tree, progress, status, logs, ...) or "all"; bare flag = all.
            # Only valid together with --run-tests (checked after the parse loop).
            val = arg.split("=", 1)[1].strip() if "=" in arg else "all"
            if not val or val.lower() == "all":
                overlay_components = list(_OVERLAY_COMPONENTS)
            else:
                requested = [c.strip() for c in val.split(",") if c.strip()]
                bad = [c for c in requested if c not in _OVERLAY_COMPONENTS]
                if bad:
                    sys.exit("--execution-overlay: unknown component(s) %s. Known: %s"
                             % (", ".join(bad), ", ".join(_OVERLAY_COMPONENTS)))
                overlay_components = requested
        elif arg.startswith("--report-level="):
            # Which report artifacts to generate (console,dom,result,screen,url).
            # When given, only the listed artifacts are produced, on success and on
            # failure alike. Validated after the parse loop. Requires --run-tests.
            report_level = arg.split("=", 1)[1].strip()
        elif arg == "--report-always":
            # Produce a full report after a successful run too, not only on failure.
            report_always = True
        elif arg.startswith("--report-screen="):
            # When screenshots are captured: start,each,finish (any combination).
            # Only has effect when 'screen' is among the report artifacts.
            report_screen = arg.split("=", 1)[1].strip()
        elif arg.startswith("--user="):
            cli_user = arg.split("=", 1)[1].strip()
        elif arg.startswith("--password="):
            cli_password = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            # An unknown or valueless option. NEVER let this reach `positional`:
            # that list supplies the URL, so a bare --run-tests used to launch
            # Chrome on the literal string "--run-tests" and run no flows at all.
            bad_option = arg
        else:
            positional.append(arg)
    level = getattr(logging, log_level.upper(), None)
    if not isinstance(level, int):
        sys.exit("Invalid --log-level %r (use DEBUG, INFO, WARNING, ERROR)." % log_level)
    logging.basicConfig(level=level, format="%(asctime)s  %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    if events_target:
        # basicConfig has already claimed stderr for log records, so events can
        # own stdout. Imported here: a launch without --events must not pay for
        # the engine package at all.
        global _events
        from engine import events as _events_module
        _events = _events_module
        _events.configure(events_target)
        atexit.register(_events.close)
    if init_config:
        init_users_json(config_path)  # writes the starter config (if absent) and exits
    if flow_command:
        run_flow_command(flow_command, flow_source, flows_dir)
    if describe_json:
        # Always valid JSON on stdout, even when it fails: the caller is a
        # program, and an exit code plus a plain-text message would leave it
        # parsing error strings.
        try:
            payload = describe(config_path, flows_dir=flows_dir,
                               sessions_dir=sessions_dir, reports_dir=reports_dir)
        except Exception as exc:  # noqa: BLE001 - the report IS the error report
            json.dump({"error": "%s: %s" % (type(exc).__name__, exc)}, sys.stdout,
                      indent=2)
            print()
            sys.exit(2)
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False, default=str)
        print()
        sys.exit(0)
    if bad_option is not None:
        sys.exit(_bad_option_message(bad_option, config_path))
    if session_prefix and (os.sep in session_prefix or (os.altsep and os.altsep in session_prefix)):
        sys.exit("--user-session must be a single name, no path separators.")
    if overlay_components and run_tests is None:
        sys.exit("--execution-overlay requires --run-tests (there is no flow "
                 "execution to visualize without it).")
    if legacy_odoo_flag == "--odoo-debug":
        log.info("%s is deprecated; extensions/ is installed by default, and "
                 "--extensions=odoo_debug names it explicitly.", legacy_odoo_flag)
    elif legacy_odoo_flag == "--no-odoo-debug":
        log.info("%s is deprecated; use --extensions to choose, or --extensions=none.",
                 legacy_odoo_flag)
    # No --extensions: install every usable extension vendored in the project. A
    # broken one is skipped with a warning rather than stopping the launch.
    if extensions is None:
        extensions = default_extensions()
    if excluded_extensions and not extensions_given:
        extensions = [e for e in extensions if e.name not in excluded_extensions]
    if flows_dir is not None and not os.path.isdir(flows_dir):
        sys.exit("--flows-dir: %s is not a directory." % flows_dir)
    if flows_dir is not None and run_tests is None and not recorder:
        sys.exit("--flows-dir requires --run-tests or --recorder (flows are only read "
                 "when scenarios run, and written when one is recorded).")
    if reports_dir is not None and run_tests is None:
        sys.exit("--reports-dir requires --run-tests (nothing is written without a run).")
    if jobs_given and run_tests is None:
        sys.exit("--jobs requires --run-tests (a plain launch just opens windows; there "
                 "are no scenario runs to spread across workers).")
    if close_after and run_tests is None:
        sys.exit("--close-after requires --run-tests (a plain launch already leaves "
                 "windows open until you close them).")
    # Build the report configuration from the --report-* flags (if any). Validate
    # here, before any window is launched, so a typo fails fast. Legacy default
    # (report_config stays None) when no report flag was given.
    report_config = None
    if report_level is not None or report_always or report_screen is not None:
        if run_tests is None:
            sys.exit("--report-level/--report-always/--report-screen require --run-tests "
                     "(reports are only produced when flows run).")
        from engine.artifacts import ReportConfig
        try:
            report_config = ReportConfig.from_cli(level=report_level, always=report_always,
                                                   screen=report_screen)
        except ValueError as exc:
            sys.exit("Error: %s" % exc)
        if report_screen is not None and not report_config.screen_enabled:
            log.warning("--report-screen has no effect: 'screen' is not in --report-level, "
                        "so no screenshots will be captured.")
    # Resolve --env before the URL check: it names the environment to launch AND
    # supplies that environment's URL, so --url only has to be typed to override it.
    envs = environments_from_config(config_path, strict=(env_name is not None
                                                         or cli_password is None))
    selected = resolve_environment(env_name, envs, config_path) if env_name is not None else None

    # --url= takes precedence, then a positional URL, then the environment's own.
    if not url and positional:
        url = positional[0]
    if not url and selected is not None:
        if not selected.origin:
            sys.exit("--env=%s selects %r, which is not a URL or host:port, so it cannot "
                     "supply the URL. Pass --url too." % (selected.alias, selected.value))
        url = selected.origin
    if not url:
        sys.exit("Usage: %s [--detach] [--env=NAME] [--user-session=PREFIX] [--sessions-dir=DIR] "
                 "[--config=FILE] [--init-users-json] [--filter-users=all|L1,L2,...] "
                 "[--extensions=LIST] [--log-level=LEVEL] "
                 "[--run-tests=all|ID,...] [--execution-overlay=all|tree,progress,status,logs] "
                 "[--report-level=console,dom,result,screen,url] [--report-always] "
                 "[--report-screen=start,each,finish] "
                 "[--close-after] [--user=LOGIN --password=PASS] (--env=NAME | --url=URL | <URL>)   "
                 "e.g. --env=dev, or --url=http://localhost:8069/web/login" % sys.argv[0])
    url = normalize_url(url)
    split = urlsplit(url)
    origin = "%s://%s" % (split.scheme, split.netloc)
    if selected is not None and selected.origin and selected.origin.lower() != origin.lower():
        # Not fatal - an explicit --url wins, e.g. a deep link into the same app -
        # but a mismatched host is nearly always a copy-paste slip worth naming.
        log.warning("--url origin %s differs from --env=%s (%s); using the URL. Profiles and "
                    "user selection still follow --env.", origin, selected.alias, selected.origin)

    # --user launches a single user, taking whatever it is not given from the
    # environment (see resolve_user_row); otherwise the list comes from the config.
    if cli_user is not None:
        if users_filter is not None:
            sys.exit("--filter-users applies to the config list; not compatible with "
                     "--user (which already names a single user). --env is fine with both.")
        config_users = [] if cli_password is not None else load_users(config_path)
        users = [resolve_user_row(cli_user, cli_password, config_users, selected,
                                  origin, envs, config_path, url)]
    else:
        users = load_users(config_path)
        if selected is not None:
            users = [u for u in users if u.env == selected.value]
        if users_filter is not None:
            # keep the config's order; error on any requested login that isn't there
            known = {u.login for u in users}
            missing = [name for name in users_filter if name not in known]
            if missing:
                sys.exit("--filter-users: unknown login(s) %s. %s has: %s"
                         % (", ".join(missing),
                            "--env=%s" % selected.alias if selected else "The config",
                            ", ".join(sorted(known))))
            wanted = set(users_filter)
            users = [u for u in users if u.login in wanted]
        if not users:
            sys.exit("No users selected (--env/--filter-users left nothing to launch).")
        if cli_password is not None:
            # Same precedence as everywhere else: what you typed beats the config.
            log.info("--password overrides the stored password for %d selected user%s.",
                     len(users), "" if len(users) == 1 else "s")
            users = apply_password_override(users, cli_password)
        spanned = {u.env for u in users}
        if selected is None and len(spanned) > 1:
            # The bug this flag exists to prevent: several environments' users, all
            # pointed at one URL. Legitimate when you really do want everything.
            log.warning("No --env: launching %d users across %d environments (%s), all pointed "
                        "at %s.", len(users), len(spanned),
                        ", ".join(sorted(env_alias(v) for v in spanned)), url)

    # Guard: two selected users must not map to the same profile folder. This can
    # happen when --user-session forces one prefix onto entries that differ only by
    # env (e.g. the same login across several environments).
    seen_dirs = {}
    for user in users:
        login = user.login
        sd = session_dir_for(session_prefix, user.env, login)
        if sd in seen_dirs:
            sys.exit("Users %r and %r both map to profile folder %r. --user-session "
                     "forces one prefix on every entry; drop it, or use --env "
                     "to launch a single environment." % (seen_dirs[sd], login, sd))
        seen_dirs[sd] = login

    # --run-tests=config is only meaningful if somebody actually has tests. Check it
    # here, before any window opens, so a typo does not cost a full launch first.
    if run_tests == "config":
        without = [u.login for u in users if not u.tests]
        if len(without) == len(users):
            sys.exit("--run-tests=config: none of the %d selected user(s) has a 'run-tests' "
                     "field in %s. Add e.g. \"run-tests\": [\"access_agent\"] to an entry, or "
                     "name the scenarios with --run-tests=ID,..."
                     % (len(users), config_path))
        if without:
            log.warning("--run-tests=config: no 'tests' configured for %s; %s will launch "
                        "but run nothing.", ", ".join(without),
                        "they" if len(without) > 1 else "it")

    # Everything above is argument/config validation, so it fails without needing a
    # browser at all; only now does a missing Chrome become the problem.
    chrome = find_chrome()
    if not chrome:
        sys.exit(chrome_missing_message())

    # Everything is resolved and valid from here on, so this event describes the
    # run a consumer is about to watch (never the passwords, which stay in the
    # config and the profile).
    _emit("launcher.start", version=version(), url=url, origin=origin,
          config=config_path, chrome=chrome, run_tests=run_tests,
          env=selected.alias if selected is not None else None,
          users=[{"login": u.login, "class": u.cls, "env": u.env, "tests": list(u.tests)}
                 for u in users])

    # In the foreground (not --detach) on Linux, bind each window's lifetime to this
    # launcher with PR_SET_PDEATHSIG, so even a hard kill of the launcher - e.g. VS
    # Code's "Stop" button, which we can't trap in Python - still closes the windows.
    # With --detach (or off Linux) just start a new session so windows outlive us.
    if detach or not sys.platform.startswith("linux"):
        spawn_kwargs = {"start_new_session": True}
    else:
        spawn_kwargs = {"preexec_fn": _preexec_die_with_parent}  # calls setsid itself
    # Chrome is a foreign executable: it must not inherit the library search path a
    # frozen build sets up for itself, or it loads our bundled libssl and dies.
    spawn_kwargs["env"] = runtime_paths.clean_subprocess_env()

    # Fetch each requested extension once (cached under the sessions dir) so every
    # profile can install it below. On any failure - offline, service down - we warn
    # and carry on without that one rather than block the logins.
    # Local (in-tree) extensions need no fetch at all; only Web Store ones are
    # downloaded, once each, cached under the sessions dir.
    ready = []              # [(name, kind, path)] installable into every profile
    store_extensions = [e for e in extensions if e.kind == "store"]
    for ext in extensions:
        if ext.kind == "local":
            ready.append((ext.name, "local", ext.value))
    if store_extensions:
        prodversion = _chrome_prodversion(chrome)
        for ext in store_extensions:
            path = os.path.join(sessions_dir, "_crx", "%s.crx" % ext.name)
            try:
                download_crx(path, ext.value, prodversion)
                ready.append((ext.name, "store", path))
            except Exception as exc:
                log.warning("Extension %s (%s) unavailable (%s); continuing without it.",
                            ext.name, ext.value, exc)

    procs = []
    sessions = []  # (cls, proc, profile, login, origin, tests) per window, for --run-tests
    for entry_prefix, cls, login, password, user_tests in users:
        # The profile folder is "<prefix>-<login>" (a single safe path segment),
        # keyed by prefix+login so each user/environment gets its own reused profile.
        # --user-session overrides the config's per-entry prefix.
        session_dir = session_dir_for(session_prefix, entry_prefix, login)
        profile = os.path.join(sessions_dir, session_dir)
        os.makedirs(profile, exist_ok=True)
        set_profile_name(profile, "%s - %s" % (cls, login))
        clear_previous_tabs(profile)  # always start with one tab, keep the login
        clear_devtools_port(profile)  # so --run-tests cannot attach to a dead/reused port
        # Install the auto-login extension into the profile (must run after
        # set_profile_name, which writes the Preferences file this merges into).
        try:
            install_autologin_extension(profile, origin, login, password)
        except Exception as exc:  # a broken extension must not block the launch
            log.warning("Skipping auto-login extension for %s: %s", login, exc)
        for ext_name, ext_kind, ext_path in ready:
            try:
                if ext_kind == "local":
                    install_local_extension(profile, ext_path, origin, ".%s_key" % ext_name)
                else:
                    install_crx_extension(profile, ext_path, origin, ".%s_key" % ext_name)
            except Exception as exc:  # optional extra; never block the launch
                log.warning("Skipping %s extension for %s: %s", ext_name, login, exc)
        try:
            # store this user's single credential in the profile's password manager
            seed_password(chrome, profile, origin, login, password)
        except Exception as exc:  # never let a save failure block the launch
            log.warning("Skipping password save for %s: %s", login, exc)
        log.info("Launching %-8s (login: %s)", cls, login)
        chrome_args = [
            chrome,
            "--user-data-dir=%s" % profile,
            "--class=%s" % cls,
            # Linux only: basic store => cookies AND saved passwords use the same
            # deterministic key, so the seeded credential is decryptable. macOS keys
            # off the login Keychain instead (see _encrypt_password).
            *_PASSWORD_STORE_ARGS,
            # The auto-login extension is installed into the profile itself
            # (install_autologin_extension), so no --load-extension is needed -
            # recent Chrome (137+) blocks that command-line switch anyway.
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
        ]
        if run_tests or recorder:
            # Flow-execution and recording only: open a CDP endpoint so the engine
            # can attach and drive this window. Port 0 = auto-pick a free port;
            # Chrome writes the chosen port to <profile>/DevToolsActivePort. A plain
            # launch never gets this switch, so its behaviour is unchanged - and the
            # port is unauthenticated, so it is not something to open by default.
            chrome_args.append("--remote-debugging-port=0")
        chrome_args.append(url)
        proc = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # own process group so we can close the whole window tree later; also
            # detaches the window so --detach can leave it running. In foreground
            # mode this also arms PR_SET_PDEATHSIG (see spawn_kwargs above).
            **spawn_kwargs,
        )
        procs.append((cls, proc))
        _emit("window.launched", login=login, cls=cls, pid=proc.pid,
              profile=profile, session=os.path.basename(profile))
        if run_tests or recorder:
            # user_tests is this user's own "tests" field; the runner uses it
            # when --run-tests=config, and ignores it otherwise.
            sessions.append((cls, proc, profile, login, origin, user_tests))
        time.sleep(0.4)

    log.info("All %d windows launched. The auto-login extension signs each one in on the "
             "login page; profiles persist the session, so later launches open already "
             "logged in.", len(procs))
    _emit("windows.ready", count=len(procs))

    if recorder:
        # Recording mode: attach to every window and show the recorder in it.
        # Nothing is captured until it is asked for, so this blocks exactly like a
        # plain launch does, and CTRL+C (or the GUI's Stop) ends it the same way,
        # closing the windows through close_all.
        from engine.recorder import record_sessions
        stop = threading.Event()

        def _stop_recording(_signum, _frame):
            stop.set()

        signal.signal(signal.SIGINT, _stop_recording)
        signal.signal(signal.SIGTERM, _stop_recording)
        rc = 1
        try:
            rc = record_sessions(sessions, env={"origin": origin, "url": url},
                                 flows_dir=flows_dir,
                                 scenario_id=recorder if recorder is not True else None,
                                 stop_event=stop)
        finally:
            _emit("recorder.exit", exit_code=rc)
            close_all(procs)
        sys.exit(rc)

    if run_tests:
        # Flow-execution mode: attach to each launched window over CDP, run the
        # requested scenarios, and write reports. Import here so a normal launch
        # never loads playwright/yaml.
        from engine.runner import run_scenarios
        rc = None   # stays None if the run raises: the event still says so
        try:
            rc = run_scenarios(sessions, run_tests, env={"origin": origin, "url": url},
                               flows_dir=flows_dir, reports_dir=reports_dir,
                               overlay_components=overlay_components, report=report_config,
                               jobs=len(sessions) if jobs == "all" else jobs)
        finally:
            _emit("run.finished", exit_code=rc)
            if close_after:
                close_all(procs)  # graceful teardown flushes each login session to disk
        if close_after:
            _emit("launcher.exit", exit_code=rc)
            sys.exit(rc)
        # Default: reports/screenshots are done, but leave the windows open so the
        # final app state (and the Execution Overlay, if enabled) can be inspected.
        # Block here just like a plain launch; exit with the aggregate result once
        # the windows are closed manually or a shutdown signal arrives.
        log.info("Execution finished (exit code %d). --close-after not set; leaving "
                 "windows open for inspection.", rc)
        keep_open_until_closed(procs)
        sys.exit(rc)

    if detach:
        log.info("--detach: leaving windows running; script exits now.")
        return

    keep_open_until_closed(procs)


if __name__ == "__main__":
    main()
