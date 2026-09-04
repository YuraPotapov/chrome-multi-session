"""cms_gui.runnertypes: the table of things the Services page can start.

These are the command lines the application will actually run, so the cases are
mostly "what argv comes out". The distinction worth holding onto is the one
between a supervised type, whose process *is* the service, and a managed one,
where the command exits immediately and the state has to be asked for.
"""

import os

import pytest

from cms_gui import runnertypes


# --------------------------------------------------------------- the registry
def test_every_type_is_reachable_by_its_own_id():
    assert set(runnertypes.BY_ID) == {t.id for t in runnertypes.TYPES}
    for runner in runnertypes.TYPES:
        assert runnertypes.get(runner.id) is runner


def test_an_id_nothing_defines_is_none_rather_than_an_error():
    # A file may name a type this build does not have; the page shows the row.
    assert runnertypes.get("from-the-future") is None
    assert "unknown" in runnertypes.label_of("from-the-future")


def test_every_type_declares_what_the_page_needs_to_draw_it():
    for runner in runnertypes.TYPES:
        assert runner.label and runner.icon
        assert runner.mode in (runnertypes.SUPERVISED, runnertypes.MANAGED)
        assert set(runner.default_settings()) == {f.key for f in runner.form_fields()}


def test_a_container_is_never_ours_to_kill():
    # Managed types force detach on: the daemon owns the container, so closing
    # the window has nothing to stop.
    for runner in runnertypes.TYPES:
        if runner.mode == runnertypes.MANAGED:
            assert runner.detach_default is True
            assert runner.detach_choice is False
            assert runner.detach_note


# ------------------------------------------------------------- splitting argv
def test_a_command_line_is_split_the_way_a_shell_would():
    assert runnertypes.split_args("-c odoo.conf --dev=all") == [
        "-c", "odoo.conf", "--dev=all"]
    assert runnertypes.split_args("") == []
    assert runnertypes.split_args("   ") == []


@pytest.mark.skipif(os.name == "nt", reason="posix lexing")
def test_quotes_hold_a_value_together():
    assert runnertypes.split_args('--name "two words"') == ["--name", "two words"]


def test_an_unbalanced_quote_is_reported_not_raised_at_the_user():
    with pytest.raises(ValueError):
        runnertypes.split_args('echo "oops')
    problems = runnertypes.BY_ID["shell"].problems({"command": 'echo "oops'})
    assert problems and "Command" in problems[0]


# ------------------------------------------------------ the working directory
def test_a_runner_without_a_directory_starts_in_the_stacks():
    assert runnertypes.resolve_dir({}, "/srv/claim") == "/srv/claim"


def test_a_relative_directory_is_read_against_the_stacks():
    assert runnertypes.resolve_dir({"dir": "addons"}, "/srv/claim") == "/srv/claim/addons"


def test_an_absolute_directory_wins_outright():
    assert runnertypes.resolve_dir({"dir": "/opt/other"}, "/srv/claim") == "/opt/other"


# -------------------------------------------------------------------- python
def test_python_runs_the_script_under_the_named_interpreter():
    runner = runnertypes.BY_ID["python"]
    argv = runner.command({"interpreter": "/venv/bin/python", "script": "odoo-bin",
                           "args": "-c odoo.conf"}, "/srv")
    assert argv == ["/venv/bin/python", "odoo-bin", "-c", "odoo.conf"]


def test_python_falls_back_to_the_platforms_own_name_for_python():
    runner = runnertypes.BY_ID["python"]
    assert runner.command({"script": "x.py"}, "")[0] == runnertypes.DEFAULT_PYTHON


def test_python_needs_a_script_and_says_so():
    assert runnertypes.BY_ID["python"].problems({}) == ["Script is required."]


def test_pythons_summary_is_the_command_a_person_would_have_typed():
    assert runnertypes.BY_ID["python"].summary(
        {"interpreter": "/venv/bin/python3", "script": "odoo-bin",
         "args": "-c odoo.conf"}) == "python3 odoo-bin -c odoo.conf"


# --------------------------------------------------------------------- shell
def test_shell_runs_the_line_without_a_shell():
    assert runnertypes.BY_ID["shell"].command({"command": "npm run dev"}, "") == [
        "npm", "run", "dev"]


def test_a_supervised_type_is_stopped_by_signalling_it_not_by_a_command():
    for name in ("python", "shell"):
        runner = runnertypes.BY_ID[name]
        assert runner.mode == runnertypes.SUPERVISED
        assert runner.stop_command({}) is None
        assert runner.probe_command({}) is None


# -------------------------------------------------------------------- docker
def test_docker_starts_stops_and_asks_after_one_container():
    runner = runnertypes.BY_ID["docker"]
    settings = {"container": "postgres-claim"}
    assert runner.command(settings, "") == ["docker", "start", "postgres-claim"]
    assert runner.stop_command(settings) == ["docker", "stop", "postgres-claim"]
    assert runner.probe_command(settings) == [
        "docker", "inspect", "-f", "{{.State.Running}}", "postgres-claim"]


def test_docker_reads_its_own_answer():
    runner = runnertypes.BY_ID["docker"]
    assert runner.probe_reads("true\n", 0) == runnertypes.RUNNING
    assert runner.probe_reads("false\n", 0) == runnertypes.STOPPED


def test_a_container_that_does_not_exist_reads_as_stopped():
    # Not as failed: the row says Stopped and Start reports the daemon's own
    # error, which names the problem better than a status column can.
    assert runnertypes.BY_ID["docker"].probe_reads("", 1) == runnertypes.STOPPED


# ------------------------------------------------------------------- compose
def test_compose_names_the_file_on_every_command():
    runner = runnertypes.BY_ID["compose"]
    settings = {"file": "/srv/claim/docker-compose.yml", "services": "db redis"}
    assert runner.command(settings, "") == [
        "docker", "compose", "-f", "/srv/claim/docker-compose.yml", "up", "-d",
        "db", "redis"]
    assert runner.stop_command(settings)[-1] == "down"
    assert "--status=running" in runner.probe_command(settings)


def test_compose_with_no_services_named_means_all_of_them():
    argv = runnertypes.BY_ID["compose"].command({"file": "c.yml"}, "")
    assert argv[-2:] == ["up", "-d"]


def test_compose_is_running_when_it_lists_any_container():
    runner = runnertypes.BY_ID["compose"]
    assert runner.probe_reads("9f2c1d\n", 0) == runnertypes.RUNNING
    assert runner.probe_reads("", 0) == runnertypes.STOPPED


def test_a_summary_survives_a_command_line_that_does_not_lex():
    # validate() reports the bad quote; it must not also stop the row drawing.
    assert runnertypes.BY_ID["compose"].summary(
        {"file": "c.yml", "services": '"oops'}) == "c.yml"


# ------------------------------------------------------- finding the venv
# The single most likely answer to "which python", and it lives in a dotted
# directory a file chooser will not list - so it is found rather than asked for.

def test_a_projects_own_venv_is_found(tmp_path):
    interpreter = tmp_path / ".venv" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    assert runnertypes.venv_python(str(tmp_path)) == str(interpreter)


def test_the_other_spellings_of_a_venv_are_found_too(tmp_path):
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    assert runnertypes.venv_python(str(tmp_path)) == str(interpreter)


def test_a_project_without_one_says_so(tmp_path):
    assert runnertypes.venv_python(str(tmp_path)) == ""
    assert runnertypes.venv_python("") == ""
    assert runnertypes.venv_python("/no/such/place") == ""


def test_a_found_venv_is_what_actually_runs(tmp_path):
    interpreter = tmp_path / ".venv" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("")
    argv = runnertypes.BY_ID["python"].command({"script": "odoo-bin"}, str(tmp_path))
    assert argv[0] == str(interpreter)


def test_a_named_interpreter_still_wins_over_the_one_found(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python3").write_text("")
    argv = runnertypes.BY_ID["python"].command(
        {"interpreter": "/usr/bin/python3.11", "script": "x.py"}, str(tmp_path))
    assert argv[0] == "/usr/bin/python3.11"


def test_every_supervised_type_can_be_told_how_long_it_may_take_to_stop():
    """Not a property of the kind: it is a property of what the kind runs.

    A shell command and a python script are each as slow to shut down as the
    thing they start, so the field is appended to every supervised form rather
    than written into each type.
    """
    for runner in runnertypes.TYPES:
        keys = {f.key for f in runner.form_fields()}
        if runner.mode == runnertypes.SUPERVISED:
            assert "stop_grace" in keys
        else:
            # A container is stopped by its daemon, which has a timeout of its own.
            assert "stop_grace" not in keys


def test_a_stop_timeout_that_is_not_a_number_is_reported():
    problems = runnertypes.stop_grace_problems({"stop_grace": "soon"})
    assert problems and "not a number" in problems[0]


def test_a_negative_stop_timeout_is_reported_with_what_to_use_instead():
    problems = runnertypes.stop_grace_problems({"stop_grace": "-1"})
    assert problems and "0 to never kill" in problems[0]


def test_a_blank_stop_timeout_is_the_default_not_an_error():
    assert runnertypes.stop_grace_problems({"stop_grace": ""}) == []
    assert runnertypes.stop_grace_problems({}) == []
