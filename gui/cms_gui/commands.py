"""The launcher's command vocabulary, declared once.

Every flag ``session_launcher.py`` accepts is described here - its group, the
kind of control that edits it, whether it needs ``--run-tests``, and the help
line it shows. The Command page renders itself from this list and
:func:`build_argv` turns a form state back into a command line, so adding a flag
to the core means adding one row here, not touching six widgets.

``tests/test_commands.py`` cross-checks this catalogue against the real
``--help`` output, so the two cannot drift apart silently.
"""

import collections

# kind:
#   flag   - a switch, present or absent
#   text   - free text (--url, --filter-users)
#   choice - one of `choices`
#   list   - comma-separated subset of `choices`
#   path   - a directory or file
#   int    - a number (or one of `choices`, e.g. "all")
Flag = collections.namedtuple(
    "Flag", "name kind group help placeholder choices needs_run_tests default")
Flag.__new__.__defaults__ = ("", None, False, None)

GENERAL = "General"
FLOW = "Flow execution"
REPORTS = "Reports"

# Order is the order the Command page renders, mirroring --help.
FLAGS = [
    Flag("--env", "choice", GENERAL,
         "Launch one environment, matched by short name. Also supplies its URL.",
         placeholder="(all environments)"),
    Flag("--filter-users", "text", GENERAL,
         "Only these logins, comma-separated.", placeholder="all"),
    Flag("--user", "text", GENERAL,
         "Launch a single user instead of the config list."),
    Flag("--password", "text", GENERAL,
         "Password to use, overriding the config's."),
    Flag("--url", "text", GENERAL,
         "Login URL; overrides the URL --env would supply.",
         placeholder="supplied by --env"),
    Flag("--user-session", "text", GENERAL,
         "Folder-name prefix for this run's profile dirs."),
    Flag("--sessions-dir", "path", GENERAL,
         "Where profiles and generated extensions are stored."),
    Flag("--extensions", "text", GENERAL,
         "Which extensions to install: names, ids, none, all.", placeholder="all"),
    Flag("--log-level", "choice", GENERAL, "Console log level.",
         choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO"),
    Flag("--detach", "flag", GENERAL,
         "Fire-and-forget: leave windows running after the launcher exits."),

    Flag("--run-tests", "text", FLOW,
         "Attach over CDP and run scenarios: all, config, or ids / tag:NAME.",
         placeholder="all"),
    Flag("--jobs", "int", FLOW,
         "Windows driven at once (1 = one after another).",
         choices=["all"], default="1", needs_run_tests=True),
    Flag("--execution-overlay", "list", FLOW,
         "In-page execution HUD components.",
         choices=["tree", "progress", "status", "logs", "highlight", "notifications"],
         needs_run_tests=True),
    Flag("--flows-dir", "path", FLOW,
         "Where scenarios, blocks and selectors.yaml live.", needs_run_tests=True),
    Flag("--reports-dir", "path", FLOW,
         "Where run artifacts are written.", needs_run_tests=True),
    Flag("--close-after", "flag", FLOW,
         "Close the windows once the run finishes.", needs_run_tests=True),

    Flag("--report-level", "list", REPORTS,
         "Which artifacts to generate.",
         choices=["console", "dom", "result", "screen", "url"], needs_run_tests=True),
    Flag("--report-screen", "list", REPORTS,
         "When screenshots are captured (needs 'screen' in --report-level).",
         choices=["start", "each", "finish"], needs_run_tests=True),
    Flag("--report-always", "flag", REPORTS,
         "Produce a full report on success too.", needs_run_tests=True),
]

BY_NAME = {f.name: f for f in FLAGS}
GROUPS = [GENERAL, FLOW, REPORTS]

# Flags the GUI drives itself rather than exposing as form controls.
GUI_OWNED = ("--config", "--events", "--describe", "--init-users-json",
             "--help", "-h", "--version", "-V")

# One-shot commands offered in the Tools menu: (label, args, needs a config).
ONE_SHOTS = [
    ("Show --help", ["--help"]),
    ("Show --version", ["--version"]),
    ("List extensions", ["--extensions=list"]),
    ("Describe (JSON)", ["--describe"]),
]


def flags_for(group):
    return [f for f in FLAGS if f.group == group]


def build_argv(state, events=True):
    """Turn a form ``state`` ({flag name: value}) into launcher arguments.

    Rules mirror the launcher's own validation so the GUI cannot build a line it
    would reject: the flow-execution and report flags are dropped unless
    ``--run-tests`` is set, and a flag left at its default is simply not passed.
    """
    args = []
    run_tests = str(state.get("--run-tests", "") or "").strip()
    for flag in FLAGS:
        if flag.needs_run_tests and not run_tests:
            continue
        value = state.get(flag.name)
        if flag.kind == "flag":
            if value:
                args.append(flag.name)
            continue
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        value = str(value or "").strip()
        if not value or value == (flag.default or ""):
            continue
        args.append("%s=%s" % (flag.name, value))
    if events:
        # Always last, and always on: the GUI's whole live view is this stream.
        args.append("--events=-")
    return args


def preview(state, core=None, events=True):
    """The command line as the user should read it (and can copy)."""
    args = build_argv(state, events=events)
    if core is not None and core.script:
        return core.display_argv(*args)
    return " ".join(["python3", "session_launcher.py"] + args)


def parse_help_flags(help_text):
    """Every ``--flag`` mentioned in the launcher's --help output.

    Used by the test that keeps this catalogue honest.
    """
    found = set()
    for line in help_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        for token in stripped.split():
            if token.startswith("--"):
                name = token.split("=")[0].rstrip(",")
                if len(name) > 2:
                    found.add(name)
                break
    return found
