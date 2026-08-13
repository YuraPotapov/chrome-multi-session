"""Launch Sessions and the saved configuration it is a working copy of.

Nothing on the page writes back to a saved configuration on its own - a run does
not save, and opening another one drops the edit - so the page has to say, at all
times, whether what is on screen is still the thing that was opened. That is what
the red Save button is, and these tests are what keeps it honest.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui.pages.launch import UNSAVED, LaunchSessionsPage
from cms_gui.settings import Settings


@pytest.fixture
def page(qapp):
    page = LaunchSessionsPage(Settings())
    yield page
    for name in list(page._configs.names()):
        page._configs.remove(name)
    page.settings.launch_config_name = ""
    page.close()


def _open_saved(page, name="Admin Access"):
    """Save what is on screen under ``name`` and select it, as the buttons do."""
    page._configs.put(name, page.state())
    page.settings.launch_config_name = name
    page._reload_configs(select=name)
    page._mark_clean()
    return name


def _marked(page):
    return {button.property("dirty") for button in (page.config_save,
                                                    page.footer_save)}


def test_a_freshly_opened_configuration_is_not_dirty(page):
    _open_saved(page)
    assert not page.is_dirty()
    assert _marked(page) == {"false"}


def test_an_edit_marks_both_save_buttons(page):
    _open_saved(page)
    page.jobs.setValue(page.jobs.value() + 3)
    assert page.is_dirty()
    assert _marked(page) == {"true"}
    assert page.config_save.toolTip() == 'Unsaved changes to "Admin Access"'


def test_saving_settles_it_again(page):
    _open_saved(page)
    page.keep_open.setChecked(not page.keep_open.isChecked())
    assert page.is_dirty()
    page.save_configuration()
    assert not page.is_dirty()
    assert _marked(page) == {"false"}
    assert page.config_save.toolTip() == ""


def test_going_back_to_the_saved_value_settles_it_too(page):
    """Dirty is "differs from the file", not "has been touched"."""
    _open_saved(page)
    before = page.jobs.value()
    page.jobs.setValue(before + 2)
    assert page.is_dirty()
    page.jobs.setValue(before)
    assert not page.is_dirty()


def test_opening_another_configuration_is_not_an_edit(page):
    _open_saved(page, "One")
    page.jobs.setValue(7)
    _open_saved(page, "Two")
    assert not page.is_dirty()
    page.config_combo.setCurrentText("One")
    assert not page.is_dirty()
    assert page.jobs.value() != 7          # One's own value came back


def test_a_page_nobody_has_touched_is_quiet(page):
    """Opening the app is not an edit, whatever the settings were left at."""
    assert page._current_config_name() == ""
    assert not page.is_dirty()
    assert _marked(page) == {"false"}


def test_an_edit_with_nothing_opened_is_flagged_too(page):
    """(unsaved) has no file behind it, which is the reason to flag it.

    These settings are stored under no name at all, so an edit to them is the
    most unsaved thing on the page. Save is what settles it - there it asks for
    a name first.
    """
    page.config_combo.setCurrentText(UNSAVED)
    page._mark_clean()
    page.jobs.setValue(page.jobs.value() + 4)
    assert page.is_dirty()
    assert _marked(page) == {"true"}
    assert "not saved" in page.config_save.toolTip()


def test_deleting_the_configuration_starts_the_settings_over(page):
    """Delete keeps what is on screen, so that becomes the new starting point."""
    name = _open_saved(page)
    page.jobs.setValue(9)
    assert page.is_dirty()
    page._configs.remove(name)             # the dialog's outcome, without the dialog
    page.settings.launch_config_name = ""
    page._reload_configs(select=UNSAVED)
    page._mark_clean()
    assert not page.is_dirty()
    page.jobs.setValue(2)
    assert page.is_dirty()


def test_an_unsaved_edit_survives_the_window_closing(qapp):
    """The working state is restored on the next start - so is the warning.

    Reopening the page puts back what was on screen, not what was in the file.
    Coming back to a Save button that had gone quiet again is how an edit gets
    lost without anyone noticing.
    """
    settings = Settings()
    first = LaunchSessionsPage(settings)
    try:
        _open_saved(first, "Overnight")
        first.jobs.setValue(first.jobs.value() + 5)
        assert first.is_dirty()
    finally:
        first.close()

    second = LaunchSessionsPage(Settings())
    try:
        assert second._current_config_name() == "Overnight"
        assert second.is_dirty()
        assert _marked(second) == {"true"}
    finally:
        for name in list(second._configs.names()):
            second._configs.remove(name)
        second.settings.launch_config_name = ""
        second.close()
