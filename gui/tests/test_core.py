"""How the GUI names the core, in a checkout and in an installed build.

Two layouts have to work: ``<python> session_launcher.py ...`` next to the GUI's
source, and ``<core-exe> ...`` shipped beside a frozen GUI. Everything else in
the front-end goes through Core.argv, so getting that shape right is what makes
the packaged app behave like the checkout.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import core as core_mod


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    """A frozen GUI with the core executable beside it, in the .deb's layout."""
    prefix = tmp_path / "opt" / "chrome-multi-session"
    gui_exe = prefix / "gui" / "chrome-multi-session-gui"
    core_exe = prefix / "core" / core_mod.CORE_EXE
    for path in (gui_exe, core_exe):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(gui_exe))
    monkeypatch.delenv(core_mod.CORE_EXE_ENV, raising=False)
    monkeypatch.setenv("CMS_HOME", str(tmp_path / "data"))
    return str(core_exe)


# ------------------------------------------------------------ what runs what

def test_a_py_file_needs_an_interpreter_and_an_executable_does_not():
    assert core_mod.needs_interpreter("/x/session_launcher.py")
    assert not core_mod.needs_interpreter("/x/chrome-multi-session-core")
    assert not core_mod.needs_interpreter("")


def test_a_checkout_runs_the_script_through_a_python():
    script, interpreter = core_mod.autodetect()
    if not script:
        pytest.skip("no core checkout next to the GUI")
    assert script.endswith("session_launcher.py")
    assert core_mod.Core(script, interpreter).argv("--describe")[:2] == [
        interpreter, script]


def test_a_packaged_core_is_the_whole_command(packaged):
    core = core_mod.Core()
    assert core.script == packaged
    assert core.interpreter == ""
    assert core.argv("--describe") == [packaged, "--describe"]
    assert core.is_configured()


def test_a_stale_interpreter_setting_cannot_derail_a_packaged_core(packaged):
    # Someone who ran the GUI from source has core/interpreter in QSettings; it
    # must not become "python chrome-multi-session-core" after they install.
    core = core_mod.Core(interpreter="/usr/bin/python3")
    assert core.argv() == [packaged]


def test_the_core_exe_env_var_wins(packaged, tmp_path):
    elsewhere = tmp_path / "other-core"
    elsewhere.write_text("", encoding="utf-8")
    os.environ[core_mod.CORE_EXE_ENV] = str(elsewhere)
    try:
        assert core_mod.Core().script == str(elsewhere)
    finally:
        del os.environ[core_mod.CORE_EXE_ENV]


# ------------------------------------------------------------- where it runs

def test_a_packaged_core_runs_in_the_users_data_directory(packaged, tmp_path):
    # Its own directory is read-only (/opt), and the config it reads by default
    # lives with the user's profiles and reports, not with the binaries.
    core = core_mod.Core()
    assert core.root == str(tmp_path / "data")
    assert core.config_path == str(tmp_path / "data" / "users.json")


def test_display_argv_shortens_both_shapes(packaged):
    assert core_mod.Core().display_argv("--describe") == (
        "%s --describe" % core_mod.CORE_EXE)
    script = core_mod.Core("/x/session_launcher.py", "/y/bin/python3")
    assert script.display_argv("--describe") == "python3 session_launcher.py --describe"


# ---------------------------------------------------------------- the browser

def test_inventory_reports_a_missing_chrome():
    inventory = core_mod.Inventory({"chrome": {"path": "", "message": "Install Chrome."}})
    assert inventory.chrome_problem() == "Install Chrome."


def test_inventory_says_nothing_when_chrome_is_there():
    assert not core_mod.Inventory(
        {"chrome": {"path": "/usr/bin/google-chrome", "version": "Chrome 151",
                    "message": ""}}).chrome_problem()


def test_inventory_reports_a_chrome_that_is_present_but_cannot_run():
    # Ubuntu's snap shim: on PATH, executable, and not a browser. The core makes
    # that call and puts the answer in "message"; the GUI just relays it.
    inventory = core_mod.Inventory(
        {"chrome": {"path": "/usr/bin/chromium-browser", "version": "",
                    "message": "…exists but does not run…"}})
    assert inventory.chrome_problem() == "…exists but does not run…"


def test_inventory_never_warns_about_a_core_that_was_not_asked():
    # An older core has no "chrome" key; that is "cannot tell", not "missing".
    assert not core_mod.Inventory({"users": []}).chrome_problem()
