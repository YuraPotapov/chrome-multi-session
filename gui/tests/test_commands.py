"""The flag catalogue and the argv it builds.

The last test here is the important one: it runs the *real* launcher's --help
and fails if the core grew a flag this GUI does not know about. That is the only
thing keeping a hand-written catalogue honest.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import commands, core as core_mod


def test_every_flag_has_a_group_and_a_help_line():
    for flag in commands.FLAGS:
        assert flag.group in commands.GROUPS, flag.name
        assert flag.help, flag.name
        assert flag.kind in ("flag", "text", "choice", "list", "path", "int"), flag.name


def test_empty_state_still_asks_for_the_event_stream():
    # The GUI's entire live view is --events=-, so it is never optional.
    assert commands.build_argv({}) == ["--events=-"]


def test_a_flag_left_at_its_default_is_not_passed():
    assert commands.build_argv({"--log-level": "INFO"}) == ["--events=-"]
    assert "--log-level=DEBUG" in commands.build_argv({"--log-level": "DEBUG"})


def test_switches_appear_without_a_value():
    args = commands.build_argv({"--detach": True})
    assert "--detach" in args and not any(a.startswith("--detach=") for a in args)


def test_flow_flags_are_dropped_without_run_tests():
    # The launcher exits on these; the GUI must not be able to build the line.
    state = {"--jobs": "4", "--close-after": True, "--execution-overlay": "all",
             "--report-level": "result", "--report-always": True}
    assert commands.build_argv(state) == ["--events=-"]


def test_flow_flags_survive_with_run_tests():
    state = {"--run-tests": "smoke", "--jobs": "4", "--close-after": True,
             "--execution-overlay": ["tree", "logs"]}
    args = commands.build_argv(state)
    assert args == ["--run-tests=smoke", "--jobs=4",
                    "--execution-overlay=tree,logs", "--close-after", "--events=-"]


def test_lists_are_joined_with_commas():
    args = commands.build_argv({"--run-tests": "x", "--report-level": ["result", "screen"]})
    assert "--report-level=result,screen" in args


def test_preview_reads_like_the_command_you_would_type():
    text = commands.preview({"--env": "localhost", "--run-tests": "smoke"})
    assert text.startswith("python3 session_launcher.py")
    assert "--env=localhost" in text and "--events=-" in text


def test_parse_help_flags_finds_option_names():
    help_text = """
Options:
  --env=NAME                Launch one environment.
  --detach                  Fire-and-forget.
  --version, -V             Print the version.
"""
    assert commands.parse_help_flags(help_text) == {"--env", "--detach", "--version"}


# --------------------------------------------------------------- the sync check

def _core():
    script, interpreter = core_mod.autodetect()
    if not script:
        pytest.skip("no core checkout next to the GUI")
    return core_mod.Core(script, interpreter)


def test_catalogue_covers_every_flag_the_core_advertises():
    """No flag may exist in --help that the Command page cannot set.

    Anything deliberately driven by the GUI itself (--config, --events, the
    exit-immediately ones) is listed in GUI_OWNED; everything else has to be in
    the catalogue.
    """
    core = _core()
    documented = commands.parse_help_flags(core.help_text())
    if not documented:
        pytest.skip("could not read --help from the core")
    missing = documented - set(commands.BY_NAME) - set(commands.GUI_OWNED)
    # Deprecated aliases the core still accepts but no longer documents as the
    # way to do things.
    missing -= {"--odoo-debug", "--no-odoo-debug", "--filter-prefix"}
    assert not missing, ("the core has flags this GUI does not offer: %s"
                         % ", ".join(sorted(missing)))


def test_no_catalogue_flag_is_unknown_to_the_core():
    core = _core()
    documented = commands.parse_help_flags(core.help_text())
    if not documented:
        pytest.skip("could not read --help from the core")
    unknown = set(commands.BY_NAME) - documented
    assert not unknown, ("this GUI offers flags the core does not accept: %s"
                         % ", ".join(sorted(unknown)))
