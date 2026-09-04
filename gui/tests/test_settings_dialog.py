"""The settings dialog: what it remembers, and what it hands the core."""

import pytest

from cms_gui import core as core_mod
from cms_gui.pages.settings_dialog import SettingsDialog
from cms_gui.settings import Settings


@pytest.fixture
def dialog(qapp):
    settings = Settings()
    # Leave nothing behind from a previous test's run.
    settings.flows_path = ""
    return SettingsDialog(settings), settings


def test_the_scenarios_folder_is_remembered(dialog):
    dialog, settings = dialog
    dialog.flows.setText("  /data/flows  ")
    dialog.apply()
    assert settings.flows_path == "/data/flows"


def test_the_scenarios_folder_reaches_the_core(dialog):
    dialog, _settings = dialog
    dialog.flows.setText("/data/flows")
    assert dialog.core().flows_dir == "/data/flows"


def test_a_blank_scenarios_folder_leaves_the_core_to_its_default(dialog):
    dialog, settings = dialog
    dialog.flows.setText("")
    dialog.apply()
    assert settings.flows_path == ""
    assert dialog.core().flows_dir == ""


def test_a_blank_field_says_where_the_scenarios_actually_go(dialog):
    # "Where do mine go right now" is the question the field exists to answer,
    # and blank is not an answer.
    dialog, _settings = dialog
    dialog.set_flows_dir("/checkout/flows")
    assert dialog.flows.placeholderText() == "/checkout/flows"


def test_a_filled_field_is_not_overwritten_by_what_is_in_force(dialog):
    dialog, _settings = dialog
    dialog.flows.setText("/mine/flows")
    dialog.set_flows_dir("/checkout/flows")
    assert dialog.flows.text() == "/mine/flows"


def test_the_dialog_opens_on_what_was_saved(qapp):
    settings = Settings()
    settings.flows_path = "/somewhere/flows"
    try:
        assert SettingsDialog(settings).flows.text() == "/somewhere/flows"
    finally:
        settings.flows_path = ""


def test_the_setting_is_what_ends_up_on_the_command_line(qapp, tmp_path):
    """The whole point of the field, end to end.

    The Scenarios page reads and writes through the core, so the tree it edits
    is whichever one this flag names - and it has to be on --describe and
    --flow-save alike, not only on a run.
    """
    script = tmp_path / "session_launcher.py"
    script.write_text("")
    settings = Settings()
    settings.flows_path = ""
    dialog = SettingsDialog(settings)
    dialog.script.setText(str(script))
    dialog.flows.setText(str(tmp_path / "flows"))
    core = dialog.core()
    assert "--flows-dir=%s" % (tmp_path / "flows") in core.argv("--describe")
    assert "--flows-dir=%s" % (tmp_path / "flows") in core.argv("--flow-save=alpha")
    assert isinstance(core, core_mod.Core)
