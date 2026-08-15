"""The two interaction levels, and the switch between them.

Launch Sessions is the interface for a user and Command is the one for a
developer, so the window has to answer two questions correctly at all times:
which of them is reachable, and which of them RUN acts on. It also has to put a
recorded run back where it came from - and the Command page's share of that is
``set_state``, which it already had: these tests are what proves the low-level
page needed no changes to gain a history.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import history as history_mod, launch, main_window as main_window_mod


@pytest.fixture
def window(qapp):
    win = main_window_mod.MainWindow()
    win.set_developer_mode(False)
    win.show()
    qapp.processEvents()
    yield win
    win.history.clear()
    win.close()


def _visible(window, key):
    """Whether a nav entry is offered (the rail itself is shown)."""
    return not window._nav_buttons[key].isHidden()


# ------------------------------------------------------------------ navigation

def test_the_navigation_has_both_groups_in_the_intended_order(window):
    assert [key for key, _l, _g in main_window_mod.CONFIGURE] == [
        "environments", "credentials", "scenarios", "commands", "launch"]
    assert [key for key, _l, _g in main_window_mod.OBSERVE] == [
        "run", "log", "artifacts", "history"]


def test_the_rail_is_wide_enough_for_every_label_it_holds(window):
    """The rail used to be a fixed 206px, which clipped once labels grew.

    "Launch Sessions" and a letter-spaced "CONFIGURE" both exceed it under a wider
    body font - a condensed family missing, a larger system font, display scaling -
    and a fixed-width QFrame clips instead of growing. So the width is measured,
    and this asserts the measurement covers the widest thing in there.
    """
    from cms_gui import widgets

    rail = window._nav_buttons["launch"].parentWidget()
    usable = rail.width() - 1          # the frame's own border-right
    for key, button in window._nav_buttons.items():
        assert button.sizeHint().width() <= usable, key

    for title in ("Configure", "Observe"):
        heading = widgets.kicker(title)
        heading.setContentsMargins(main_window_mod.RAIL_PADDING, 8,
                                   main_window_mod.RAIL_PADDING, 6)
        # Less the trailing letter-spacing, which is measured but never drawn.
        ink = heading.sizeHint().width() - main_window_mod.HEADING_TRIM
        assert ink <= rail.width(), title


def test_the_rail_never_shrinks_below_its_floor(window):
    rail = window._nav_buttons["launch"].parentWidget()
    assert rail.width() >= main_window_mod.RAIL_MIN_WIDTH


def test_the_sidebar_divider_runs_unbroken_down_the_whole_rail(window, qapp):
    """Nothing in the rail may paint over the frame's own border.

    Regression, two causes at once: the global `QWidget { background }` rule gave
    every QLabel an opaque fill, so the CONFIGURE and OBSERVE headings punched
    holes in the divider, and the active nav item's accent fill punched another.
    A stylesheet border is painted inside the widget rect and children are not
    clipped to the contents rect, so full-width children have to leave it a pixel.
    """
    from cms_gui import theme

    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    qapp.processEvents()

    rail = window._nav_buttons["launch"].parentWidget()
    image = rail.grab().toImage()
    edge = rail.width() - 1
    wrong = {}
    for y in range(image.height()):
        name = image.pixelColor(edge, y).name().lower()
        if name != theme.DIVIDER.lower():
            wrong[name] = wrong.get(name, 0) + 1
    assert not wrong, "the divider is broken by %s" % wrong


def test_a_group_heading_sits_on_the_sidebar_tint_not_the_page_colour(window, qapp):
    from cms_gui import theme

    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    qapp.processEvents()

    rail = window._nav_buttons["launch"].parentWidget()
    image = rail.grab().toImage()
    # A few pixels left of the "CONFIGURE" text, inside its row.
    counts = {}
    for y in range(14, 30):
        for x in range(4, 12):
            name = image.pixelColor(x, y).name().lower()
            counts[name] = counts.get(name, 0) + 1
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    assert dominant == theme.NEUTRAL[100].lower()


def test_the_window_has_the_application_icon(window):
    assert not window.windowIcon().isNull()


def test_launch_sessions_is_offered_in_both_modes(window):
    assert _visible(window, "launch")
    window.set_developer_mode(True)
    assert _visible(window, "launch")


def test_history_is_offered_in_both_modes(window):
    assert _visible(window, "history")
    window.set_developer_mode(True)
    assert _visible(window, "history")


def test_command_is_only_offered_in_developer_mode(window):
    assert not _visible(window, "commands")
    window.set_developer_mode(True)
    assert _visible(window, "commands")
    window.set_developer_mode(False)
    assert not _visible(window, "commands")


def test_the_cli_facing_toolbar_button_follows_the_mode(window):
    # "Copy command" has nothing to offer someone who never sees a command.
    assert window.copy_button.isHidden()
    window.set_developer_mode(True)
    assert not window.copy_button.isHidden()


def test_the_mode_is_readable_from_the_button_itself(window):
    assert "off" in window.developer_button.text()
    assert not window.developer_button.isChecked()
    window.set_developer_mode(True)
    assert "on" in window.developer_button.text()
    assert window.developer_button.isChecked()
    assert window.developer_button.property("variant") == "primary"


def test_the_menu_item_and_the_button_stay_in_step(window):
    window.developer_action.setChecked(True)
    assert window.developer_button.isChecked()
    assert window.settings.developer_mode
    window.developer_button.setChecked(False)
    assert not window.developer_action.isChecked()
    assert not window.settings.developer_mode


def test_the_mode_survives_a_restart(window, qapp):
    window.set_developer_mode(True)
    again = main_window_mod.MainWindow()
    try:
        assert again.settings.developer_mode
        assert _visible(again, "commands")
    finally:
        again.set_developer_mode(False)
        again.close()


def test_leaving_developer_mode_while_on_the_command_page_moves_you_off_it(window):
    window.set_developer_mode(True)
    window.show_page("commands")
    assert window.stack.currentWidget() is window.command
    window.set_developer_mode(False)
    assert window.stack.currentWidget() is window.launch


def test_the_command_page_cannot_be_reached_in_regular_mode(window):
    window.show_page("commands")
    assert window.stack.currentWidget() is window.launch


def test_an_unknown_page_key_lands_on_launch_sessions(window):
    window.show_page("no-such-page")
    assert window.stack.currentWidget() is window.launch


# ------------------------------------------------------------------ which page runs

def test_run_acts_on_launch_sessions_in_regular_mode(window):
    assert window._run_page()[0] == "launch"
    assert window._run_page()[1] is window.launch


def test_run_follows_the_last_launching_page_you_visited(window):
    window.set_developer_mode(True)
    window.show_page("commands")
    assert window._run_source == "commands"
    assert window._run_page()[1] is window.command
    window.show_page("launch")
    assert window._run_source == "launch"
    assert window._run_page()[1] is window.launch


def test_visiting_an_observing_page_does_not_change_what_run_does(window):
    window.show_page("launch")
    window.show_page("log")
    assert window._run_source == "launch"


def test_command_stops_being_the_run_target_when_developer_mode_goes_off(window):
    window.set_developer_mode(True)
    window.show_page("commands")
    window.set_developer_mode(False)
    # Nothing can run a page the user cannot see.
    assert window._run_page()[1] is window.launch


# ------------------------------------------------------------------ history

def test_restoring_a_command_entry_reproduces_the_form_exactly(window):
    """The Command page contributes ``set_state`` and nothing else.

    Restoring is the one thing history asks of the low-level page, and it was
    already part of its API - which is why the page itself is untouched by all of
    this.
    """
    state = dict(window.command.state())
    state.update({"--env": "localhost:8069", "--run-tests": "all",
                  "--jobs": "3", "--detach": True})
    entry = {"kind": history_mod.COMMAND, "command_state": state}

    assert window.restore_entry(entry) == "commands"
    assert window.stack.currentWidget() is window.command
    restored = window.command.state()
    for name in ("--env", "--run-tests", "--jobs", "--detach"):
        assert restored[name] == state[name], name


def test_restoring_a_command_entry_turns_developer_mode_on(window):
    assert not window.settings.developer_mode
    window.restore_entry({"kind": history_mod.COMMAND,
                          "command_state": {"--env": "localhost:8069"}})
    # Otherwise it would restore into a page the user has no way to look at.
    assert window.settings.developer_mode
    assert window.stack.currentWidget() is window.command


def test_restoring_a_launch_entry_reproduces_the_configuration(window):
    config = launch.merged({
        "environment": "",
        "users": {"mode": launch.USERS_PICK, "logins": ["admin"]},
        "scenarios": {"mode": launch.SCENARIOS_PICK, "selected": ["auth.login"]},
        "sessions": {"jobs": 4, "all_at_once": False, "keep_open": False,
                     "detach": False},
        "screenshots": {"mode": launch.SHOTS_FINISH}})

    assert window.restore_entry({"kind": history_mod.LAUNCH,
                                 "launch_config": config}) == "launch"
    assert window.stack.currentWidget() is window.launch
    restored = window.launch.state()
    assert restored["users"] == {"mode": launch.USERS_PICK, "logins": ["admin"]}
    assert restored["scenarios"]["selected"] == ["auth.login"]
    assert restored["sessions"]["jobs"] == 4
    assert restored["sessions"]["keep_open"] is False
    assert restored["screenshots"]["mode"] == launch.SHOTS_FINISH
    # And it means the same thing it meant when it ran.
    assert window.launch.argv() == launch.argv(config, window.inventory)


def test_a_restored_launch_entry_keeps_a_selection_the_core_no_longer_offers(window):
    """A scenario that has been deleted must not silently vanish from a config.

    Dropping it would turn "run these three" into "run these two" the moment the
    entry was opened, with nothing said about it.
    """
    config = launch.merged({"scenarios": {"mode": launch.SCENARIOS_PICK,
                                          "selected": ["ghost.scenario"]}})
    window.restore_entry({"kind": history_mod.LAUNCH, "launch_config": config})
    assert window.launch.state()["scenarios"]["selected"] == ["ghost.scenario"]


def test_a_run_is_recorded_before_it_starts_and_closed_when_it_ends(window):
    entry_id = window.history.begin(history_mod.LAUNCH,
                                   {"summary": "localhost:8069 · admin"})
    window._entry_id = entry_id
    window._stopping = False
    window._close_entry(history_mod.status_for(0), exit_code=0)

    entry = window.history.entry(entry_id)
    assert entry["status"] == history_mod.OK
    assert entry["exit_code"] == 0
    assert entry["finished_at"]
    # The log is archived at the end of the run, because the next run clears it.
    assert entry["log_file"] and os.path.isfile(entry["log_file"])


def test_stopping_a_run_is_recorded_as_stopped_not_failed(window):
    entry_id = window.history.begin(history_mod.LAUNCH, {})
    window._entry_id = entry_id
    window._stopping = True
    window._close_entry(history_mod.status_for(130, stopped=True), exit_code=130)
    assert window.history.entry(entry_id)["status"] == history_mod.STOPPED


def test_closing_an_entry_twice_does_nothing_the_second_time(window):
    entry_id = window.history.begin(history_mod.LAUNCH, {})
    window._entry_id = entry_id
    window._close_entry(history_mod.OK, exit_code=0)
    window._close_entry(history_mod.FAILED, exit_code=1)
    assert window.history.entry(entry_id)["status"] == history_mod.OK


def test_the_history_page_shows_runs_from_both_pages(window, qapp):
    window.history.begin(history_mod.LAUNCH,
                         {"launch_config": {"environment": "localhost:8069"}})
    window.history.begin(history_mod.COMMAND,
                         {"command_state": {"--env": "staging", "--run-tests": "all"}})
    qapp.processEvents()
    page = window.history_page
    assert page.table.rowCount() == 2

    page.filter.set_current("Command")
    assert page.table.rowCount() == 1
    assert page.table.item(0, 2).text() == "staging"
    assert page.table.item(0, 4).text() == "all"

    page.filter.set_current("Launch Sessions")
    assert page.table.rowCount() == 1
    assert "localhost:8069" in page.table.item(0, 2).text()

    page.filter.set_current("All")
    assert page.table.rowCount() == 2


def test_the_history_page_describes_a_launch_entry_without_naming_a_flag(window):
    from cms_gui.pages import history as history_page

    entry = {"kind": history_mod.LAUNCH, "launch_config": launch.merged(
        {"environment": "localhost:8069",
         "scenarios": {"mode": launch.SCENARIOS_ALL, "selected": []}})}
    text = " ".join("%s %s" % row for row in history_page.detail_rows(entry))
    assert "--" not in text


def test_the_history_page_describes_a_command_entry_with_the_flags_it_used(window):
    from cms_gui.pages import history as history_page

    entry = {"kind": history_mod.COMMAND,
             "command_state": {"--env": "localhost:8069", "--detach": True,
                               "--log-level": "INFO", "--url": ""}}
    rows = dict(history_page.detail_rows(entry))
    assert rows["--env"] == "localhost:8069"
    assert rows["--detach"] == "on"
    # A flag left at its default, or empty, was not part of the command.
    assert "--log-level" not in rows
    assert "--url" not in rows


# ------------------------------------------------- continuing a recording
# Recording usually means adding to something half-written, so RUN's menu has to
# offer that - and has to name the file it would write to, since appending to
# somebody's scenario is not a thing to do by accident.

def _inventory_with(window, *rows):
    from cms_gui import core as core_mod

    inventory = core_mod.Inventory({"scenarios": list(rows), "blocks": [],
                                    "selectors": {}, "envs": [], "users": []})
    window.inventory = inventory
    window.scenarios.set_inventory(inventory)
    return inventory


def _scenario_row(flow_id, writable=True):
    return {"id": flow_id, "name": flow_id, "tags": [], "writable": writable,
            "source": "user" if writable else "bundled", "in_all": True}


def test_with_nothing_selected_a_recording_is_new_and_asks_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec",
                        lambda *a, **k: pytest.fail("asked when there was nothing to ask"))
    _inventory_with(window, _scenario_row("mine"))
    assert window.recordable_scenario() == ""
    assert window.ask_recording_target() == ("new", "")


def test_the_scenario_open_in_the_editor_is_what_continues(window):
    _inventory_with(window, _scenario_row("mine"))
    window.scenarios.current = {"id": "mine", "writable": True}
    assert window.recordable_scenario() == "mine"


def test_a_bundled_scenario_is_never_offered(window):
    # It cannot be written back, so recording into it would collect steps and
    # then throw them away.
    _inventory_with(window, _scenario_row("shipped", writable=False))
    window.scenarios.current = {"id": "shipped", "writable": False}
    assert window.recordable_scenario() == ""


def test_exactly_one_scenario_chosen_on_launch_sessions_counts(window):
    _inventory_with(window, _scenario_row("picked"), _scenario_row("other"))
    window.scenarios.current = None
    window.launch.set_state({"scenarios": {"mode": "pick", "selected": ["picked"]}})
    assert window.recordable_scenario() == "picked"


def test_two_chosen_scenarios_are_ambiguous_so_neither_is_used(window):
    _inventory_with(window, _scenario_row("a"), _scenario_row("b"))
    window.scenarios.current = None
    window.launch.set_state({"scenarios": {"mode": "pick", "selected": ["a", "b"]}})
    assert window.recordable_scenario() == ""


def test_a_selected_scenario_is_confirmed_before_being_added_to(window, monkeypatch):
    """Appending to a scenario and replacing one look identical until too late."""
    from PySide6.QtWidgets import QMessageBox

    _inventory_with(window, _scenario_row("mine"))
    window.scenarios.current = {"id": "mine", "writable": True}

    seen = {}

    def answer(box):
        seen["text"] = box.text()
        # The default is the safe one: adding to what is there. Clicking the
        # button itself is what sets clickedButton(), which is what is read.
        chosen = box.defaultButton()
        seen["default"] = chosen.text()
        chosen.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", answer)
    assert window.ask_recording_target() == ("continue", "mine")
    assert "mine" in seen["text"]
    assert "mine" in seen["default"]


def test_start_new_is_offered_even_when_something_is_selected(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _inventory_with(window, _scenario_row("mine"))
    window.scenarios.current = {"id": "mine", "writable": True}

    def choose_new(box):
        for button in box.buttons():
            if button.text() == "Start new":
                button.click()
                return 0
        pytest.fail("no way to start a new scenario")

    monkeypatch.setattr(QMessageBox, "exec", choose_new)
    assert window.ask_recording_target() == ("new", "")


def test_cancelling_records_nothing(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    _inventory_with(window, _scenario_row("mine"))
    window.scenarios.current = {"id": "mine", "writable": True}

    def cancel(box):
        box.button(QMessageBox.Cancel).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", cancel)
    assert window.ask_recording_target()[0] == "cancel"

    started = []
    monkeypatch.setattr(window, "start_run", lambda **kw: started.append(kw))
    window.start_recording()
    assert started == []
