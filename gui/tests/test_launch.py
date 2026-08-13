"""The translation from a user's answers to a launcher command line.

This is where the Launch Sessions page's promise is kept or broken: the user
never sees a flag, so every rule the launcher has about how flags combine has to
be applied here instead. These tests pin the ones a user could not possibly
guess - screenshots needing ``screen`` in the report level, the flow and report
flags being meaningless without scenarios - and the one rule that keeps the two
pages honest: the argv comes out of ``commands.build_argv``, not from here.
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import commands, core, launch


def _config(**sections):
    """A default configuration with some sections replaced."""
    config = copy.deepcopy(launch.DEFAULTS)
    config.update(sections)
    return config


def _flag(argv, name):
    """The value of ``--name=value`` in ``argv``, "" for a bare flag, None if absent."""
    for arg in argv:
        if arg == name:
            return ""
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


# ------------------------------------------------------------------ the defaults

def test_the_default_configuration_asks_for_nothing_but_the_event_stream():
    # Every default is the core's own, so a fresh page runs the plainest possible
    # launch: open the windows, sign in, touch nothing else.
    assert launch.argv(launch.DEFAULTS) == ["--events=-"]


def test_every_command_line_ends_with_the_event_stream():
    argv = launch.argv(_config(scenarios={"mode": launch.SCENARIOS_ALL,
                                          "selected": []}))
    assert argv[-1] == "--events=-"


def test_a_partial_configuration_is_filled_in_from_the_defaults():
    # A configuration saved by an older version has no "overlay" section at all.
    config = {"environment": "localhost:8069"}
    assert launch.merged(config)["overlay"] == launch.DEFAULTS["overlay"]
    assert _flag(launch.argv(config), "--env") == "localhost:8069"


def test_merging_does_not_mutate_the_defaults():
    launch.merged({"users": {"mode": launch.USERS_PICK, "logins": ["admin"]}})
    assert launch.DEFAULTS["users"]["logins"] == []


# ------------------------------------------------------------------ selection

def test_choosing_accounts_filters_them_and_choosing_all_does_not():
    picked = _config(users={"mode": launch.USERS_PICK,
                            "logins": ["admin", "manager1"]})
    assert _flag(launch.argv(picked), "--filter-users") == "admin,manager1"
    assert _flag(launch.argv(launch.DEFAULTS), "--filter-users") is None


def test_extensions_all_passes_no_flag_but_none_has_to_say_so():
    # "all" is the core's default set, so silence is the right way to ask for it;
    # "none" is an instruction and has to be spoken.
    assert _flag(launch.argv(launch.DEFAULTS), "--extensions") is None
    none = _config(extensions={"mode": launch.EXT_NONE, "names": []})
    assert _flag(launch.argv(none), "--extensions") == "none"
    picked = _config(extensions={"mode": launch.EXT_PICK,
                                 "names": ["odoo_debug", "helper"]})
    assert _flag(launch.argv(picked), "--extensions") == "odoo_debug,helper"


def test_each_scenario_mode_maps_to_what_the_core_expects():
    def run_tests(mode, selected=()):
        return _flag(launch.argv(_config(
            scenarios={"mode": mode, "selected": list(selected)})), "--run-tests")

    assert run_tests(launch.SCENARIOS_NONE) is None
    assert run_tests(launch.SCENARIOS_ALL) == "all"
    assert run_tests(launch.SCENARIOS_PER_USER) == "config"
    assert run_tests(launch.SCENARIOS_PICK, ["auth.login", "tag:smoke"]) \
        == "auth.login,tag:smoke"


def test_sessions_at_once_and_closing_afterwards():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     sessions={"jobs": 4, "all_at_once": False,
                               "keep_open": False, "detach": False})
    argv = launch.argv(config)
    assert _flag(argv, "--jobs") == "4"
    assert "--close-after" in argv

    config["sessions"] = {"jobs": 4, "all_at_once": True,
                          "keep_open": True, "detach": True}
    argv = launch.argv(config)
    assert _flag(argv, "--jobs") == "all"
    assert "--close-after" not in argv
    assert "--detach" in argv


def test_one_session_at_a_time_is_the_default_and_is_not_passed():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []})
    assert _flag(launch.argv(config), "--jobs") is None


# ------------------------------------------------------------------ the rules a
# user could not guess

def test_asking_for_screenshots_adds_the_artifact_that_makes_them_happen():
    """The core ignores --report-screen unless 'screen' is a requested artifact.

    A user ticking "screenshots" and silently getting none is the exact failure
    this page exists to prevent, so the artifact is added on their behalf.
    """
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     screenshots={"mode": launch.SHOTS_FINISH})
    argv = launch.argv(config)
    assert _flag(argv, "--report-screen") == "finish"
    assert "screen" in (_flag(argv, "--report-level") or "").split(",")


def test_screenshots_added_to_a_chosen_artifact_set_keep_the_canonical_order():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     reports={"level": launch.REPORTS_CUSTOM,
                              "artifacts": ["url", "console"], "always": False},
                     screenshots={"mode": launch.SHOTS_EACH})
    level = (_flag(launch.argv(config), "--report-level") or "").split(",")
    assert level == ["console", "screen", "url"]
    assert _flag(launch.argv(config), "--report-screen") == "each"


def test_screenshots_off_never_asks_for_the_screen_artifact_on_its_own():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     reports={"level": launch.REPORTS_CUSTOM,
                              "artifacts": ["result"], "always": False})
    argv = launch.argv(config)
    assert _flag(argv, "--report-level") == "result"
    assert _flag(argv, "--report-screen") is None


def test_full_diagnostics_asks_for_every_artifact():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     reports={"level": launch.REPORTS_FULL, "artifacts": [],
                              "always": True})
    argv = launch.argv(config)
    assert set((_flag(argv, "--report-level") or "").split(",")) \
        == set(launch.ALL_ARTIFACTS)
    assert "--report-always" in argv


def test_nothing_scripted_drops_every_flag_that_would_be_rejected():
    """No scenarios means no flow run, and the core refuses the flow flags then.

    The page lets a user tick screenshots and 8-at-once before deciding not to
    script anything; the resulting command must still be one the launcher accepts.
    """
    config = _config(sessions={"jobs": 8, "all_at_once": False,
                               "keep_open": False, "detach": False},
                     reports={"level": launch.REPORTS_FULL, "artifacts": [],
                              "always": True},
                     screenshots={"mode": launch.SHOTS_EACH},
                     overlay={"enabled": True, "components": []})
    argv = launch.argv(config)
    for flag in ("--jobs", "--report-level", "--report-screen",
                 "--execution-overlay", "--flows-dir", "--reports-dir"):
        assert _flag(argv, flag) is None, flag
    assert "--close-after" not in argv
    assert "--report-always" not in argv
    # And it says so, rather than dropping them behind the user's back.
    assert any("not apply" in note for note in launch.notes(config))


def test_the_overlay_enabled_with_nothing_ticked_means_the_whole_hud():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     overlay={"enabled": True, "components": []})
    assert _flag(launch.argv(config), "--execution-overlay") == ",".join(
        launch.ALL_OVERLAY)


def test_advanced_overrides_reach_the_command_line():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     advanced={"url": "http://localhost:8069/web/login",
                               "profile_prefix": "matrix", "log_level": "DEBUG",
                               "flows_dir": "/tmp/flows", "reports_dir": "/tmp/out",
                               "sessions_dir": "/tmp/profiles"})
    argv = launch.argv(config)
    assert _flag(argv, "--url") == "http://localhost:8069/web/login"
    assert _flag(argv, "--user-session") == "matrix"
    assert _flag(argv, "--log-level") == "DEBUG"
    assert _flag(argv, "--flows-dir") == "/tmp/flows"


def test_the_default_log_level_is_not_passed():
    assert _flag(launch.argv(launch.DEFAULTS), "--log-level") is None


# ------------------------------------------------------------------ one argv builder

def test_the_command_line_comes_from_the_shared_builder():
    """Launch Sessions must not grow an argv builder of its own.

    Both pages produce the same kind of state dict and hand it to the same
    function; if that ever stopped being true, a selection would mean two
    different things depending on which page made it.
    """
    config = _config(environment="localhost:8069",
                     scenarios={"mode": launch.SCENARIOS_ALL, "selected": []})
    state = launch.to_command_state(config)
    assert launch.argv(config) == commands.build_argv(state)
    # And every key it produces is a flag the catalogue knows about.
    assert set(state) <= set(commands.BY_NAME)


def test_the_state_it_produces_covers_every_flag_the_command_page_offers():
    state = launch.to_command_state(launch.DEFAULTS)
    developer_only = {"--user", "--password"}
    missing = [f.name for f in commands.FLAGS
               if f.name not in state and f.name not in developer_only]
    assert not missing, "no way to reach: %s" % ", ".join(missing)


# ------------------------------------------------------------------ validation

def test_choosing_scenarios_without_choosing_any_is_a_problem():
    config = _config(scenarios={"mode": launch.SCENARIOS_PICK, "selected": []})
    assert any("scenario" in p for p in launch.validate(config))
    config["scenarios"]["selected"] = ["auth.login"]
    assert launch.validate(config) == []


def test_choosing_accounts_without_choosing_any_is_a_problem():
    config = _config(users={"mode": launch.USERS_PICK, "logins": []})
    assert any("account" in p for p in launch.validate(config))


def test_choosing_artifacts_without_choosing_any_is_a_problem():
    config = _config(scenarios={"mode": launch.SCENARIOS_ALL, "selected": []},
                     reports={"level": launch.REPORTS_CUSTOM, "artifacts": [],
                              "always": False})
    assert any("artifact" in p for p in launch.validate(config))


def test_an_account_missing_from_the_chosen_environment_is_reported():
    inventory = core.Inventory({
        "envs": [{"alias": "localhost:8069", "value": "localhost:8069",
                  "origin": "http://localhost:8069", "count": 1}],
        "users": [{"env": "localhost:8069", "login": "admin", "class": "admin"}],
    })
    config = _config(environment="localhost:8069",
                     users={"mode": launch.USERS_PICK,
                            "logins": ["admin", "ghost"]})
    problems = launch.validate(config, inventory)
    assert any("ghost" in p for p in problems)
    # The account that does exist is not complained about.
    assert not any("admin," in p or p.endswith("admin.") for p in problems)


def test_an_unknown_environment_is_reported():
    inventory = core.Inventory({"envs": [{"alias": "localhost:8069"}]})
    problems = launch.validate(_config(environment="staging"), inventory)
    assert any("staging" in p for p in problems)


def test_the_default_configuration_has_nothing_to_fix():
    assert launch.validate(launch.DEFAULTS) == []


# ------------------------------------------------------------------ how it reads

def test_the_summary_speaks_in_the_user_s_words():
    config = _config(environment="localhost:8069",
                     users={"mode": launch.USERS_PICK, "logins": ["admin"]},
                     scenarios={"mode": launch.SCENARIOS_ALL, "selected": []})
    text = " ".join("%s %s" % row for row in launch.summarise(config))
    for flag in ("--env", "--filter-users", "--run-tests", "--report-level"):
        assert flag not in text
    assert "admin" in text and "Every scenario" in text.replace("Run every", "Every")


def test_the_summary_hides_report_rows_when_nothing_is_scripted():
    labels = [label for label, _value in launch.summarise(launch.DEFAULTS)]
    assert "Screenshots" not in labels
    assert "Reports" not in labels


def test_a_history_line_names_the_four_things_that_identify_a_run():
    config = _config(environment="localhost:8069",
                     users={"mode": launch.USERS_PICK, "logins": ["admin"]},
                     scenarios={"mode": launch.SCENARIOS_PICK,
                                "selected": ["auth.login"]})
    line = launch.describe_line(config)
    assert "localhost:8069" in line and "admin" in line and "auth.login" in line
