"""The Log sources page: connections and logs for --server-log.

Rows are created and edited in a dialog, so most of these drive
:class:`ConnectionDialog` / :class:`LogDialog` directly - that is where the fields
live, and where a half-filled row is caught. The page's own cases are about the
overview, the file, and what Save is allowed to do.
"""

import json
import os
import sys

import pytest

from cms_gui import logsourcesfile as lsf
from cms_gui.pages.logsources import (ConnectionDialog, LogDialog,
                                      LogSourcesPage, LogViewerDialog)

# The core is one directory up and not installed. The GUI never imports it at
# runtime; a test may, to check the two agree about the file.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

LOCAL = "localhost:8069"
DEV = "https://dev.example.com/"


@pytest.fixture
def page(qapp, tmp_path):
    path = tmp_path / "logsources.json"
    path.write_text(json.dumps({
        "connections": [{"name": "here", "type": "local"},
                        {"name": "dev", "type": "ssh", "host": "dev.example.com",
                         "user": "deploy"}],
        "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                  "type": "file", "path": "/var/log/app.log", "format": "django",
                  "default": True},
                 {"name": "nginx", "connection": "dev", "env": DEV, "type": "file",
                  "path": "/var/log/nginx/error.log", "format": "nginx"}]}))
    widget = LogSourcesPage()
    widget.set_environments([LOCAL, DEV])
    widget.load(str(path))
    yield widget
    widget.close()


@pytest.fixture
def empty_page(qapp, tmp_path):
    widget = LogSourcesPage()
    widget.set_environments([LOCAL, DEV])
    widget.load(str(tmp_path / "logsources.json"))
    yield widget
    widget.close()


# ------------------------------------------------------------------- overview
def test_the_file_arrives_in_both_tables(page):
    connections, logs = page.rows()
    assert [c.name for c in connections] == ["here", "dev"]
    assert [l.name for l in logs] == ["app", "nginx"]
    assert page.connections.rowCount() == 2 and page.logs.rowCount() == 2


def test_the_overview_says_where_and_what_rather_than_raw_columns(page):
    assert page.connections.item(1, page.C_WHERE).text() == "ssh deploy@dev.example.com"
    assert page.connections.item(0, page.C_USED).text() == "1 log"
    assert page.logs.item(0, page.L_READS).text() == "file  /var/log/app.log"
    assert page.logs.item(0, page.L_DEFAULT).text() == "yes"


def test_the_tables_are_not_edited_in_place(page):
    from PySide6.QtWidgets import QAbstractItemView

    # Every edit goes through a dialog; a cell that looks editable and is not
    # would be worse than one that plainly is not.
    assert page.connections.editTriggers() == QAbstractItemView.NoEditTriggers
    assert page.logs.editTriggers() == QAbstractItemView.NoEditTriggers


def test_an_empty_file_says_what_to_do_first(empty_page):
    assert "Add a connection" in empty_page.status.text()
    assert empty_page.save_button.isEnabled()


# --------------------------------------------------------- connection dialog
def test_a_new_connection_starts_local_and_invalid_until_named(qapp):
    dialog = ConnectionDialog()
    assert dialog.value().type == "local"
    assert not dialog.buttons.buttons()[0].isEnabled()      # no name yet
    dialog.name.setText("here")
    assert dialog.value().name == "here"
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_ssh_fields_appear_only_for_an_ssh_connection(qapp):
    dialog = ConnectionDialog()
    dialog.name.setText("dev")
    assert not dialog.host_field.isVisibleTo(dialog)
    dialog.type.setCurrentText("ssh")
    # A form can show the fields that apply; a fixed grid of columns cannot.
    assert dialog.host_field.isVisibleTo(dialog)
    assert "needs a host" in dialog.problem.text()
    dialog.host.setText("dev.example.com")
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_a_local_connection_does_not_carry_ssh_leftovers(qapp):
    dialog = ConnectionDialog()
    dialog.name.setText("here")
    dialog.type.setCurrentText("ssh")
    dialog.host.setText("somewhere")
    dialog.type.setCurrentText("local")
    assert "host" not in dialog.value().to_entry()
    dialog.close()


def test_a_duplicate_connection_name_is_caught_in_the_dialog(qapp):
    dialog = ConnectionDialog(taken=["here"])
    dialog.name.setText("here")
    assert "already used" in dialog.problem.text()
    assert not dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_editing_a_connection_does_not_collide_with_its_own_name(qapp):
    row = lsf.ConnectionRow(name="here", type="local")
    dialog = ConnectionDialog(row, taken=["here", "dev"])
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_a_port_that_is_not_a_number_is_caught(qapp):
    dialog = ConnectionDialog()
    dialog.name.setText("dev")
    dialog.type.setCurrentText("ssh")
    dialog.host.setText("h")
    dialog.port.setText("twenty-two")
    assert "port must be a number" in dialog.problem.text()
    dialog.close()


# ---------------------------------------------------------------- log dialog
def _connections():
    return [lsf.ConnectionRow(name="here", type="local"),
            lsf.ConnectionRow(name="dev", type="ssh", host="h")]


def test_a_new_log_is_invalid_until_it_has_a_name_target_and_environment(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL, DEV])
    ok = dialog.buttons.buttons()[0]
    assert not ok.isEnabled()
    dialog.name.setText("app")
    dialog.target.setText("/var/log/app.log")
    assert not ok.isEnabled()                     # still no environment
    dialog.envs.set_checked([LOCAL], notify=True)
    assert ok.isEnabled()
    assert dialog.value().envs == [LOCAL]
    dialog.close()


def test_the_target_field_is_relabelled_for_each_kind(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    assert dialog.target_field.label.text() == "Path"
    dialog.type.setCurrentText("docker")
    assert dialog.target_field.label.text() == "Container"
    assert "docker logs" in dialog.target_hint.text()
    dialog.type.setCurrentText("http")
    assert dialog.target_field.label.text() == "URL"
    dialog.close()


def test_only_a_local_file_offers_a_browse_button(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    assert dialog.target_browse.isVisibleTo(dialog)
    dialog.type.setCurrentText("docker")
    assert not dialog.target_browse.isVisibleTo(dialog)
    dialog.close()


def test_a_journal_needs_no_unit(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.name.setText("system")
    dialog.envs.set_checked([LOCAL], notify=True)
    dialog.type.setCurrentText("journal")
    # Empty means the whole journal, which is a reasonable thing to ask for.
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_an_environment_not_in_the_inventory_can_still_be_named(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.name.setText("app")
    dialog.target.setText("/x.log")
    dialog.other_envs.setText("https://brand-new.example.com/")
    assert dialog.value().envs == ["https://brand-new.example.com/"]
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_a_name_already_used_in_the_same_environment_is_caught(qapp):
    existing = lsf.LogRow(name="app", connection="here", envs=[LOCAL],
                          target="/a.log")
    dialog = LogDialog(connections=_connections(), envs=[LOCAL, DEV],
                       siblings=[existing])
    dialog.name.setText("app")
    dialog.target.setText("/b.log")
    dialog.envs.set_checked([LOCAL], notify=True)
    assert "already used" in dialog.problem.text()
    # ...but the same name on another stand is exactly how "app" exists everywhere.
    dialog.envs.set_checked([DEV], notify=True)
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_a_log_cannot_be_added_before_a_connection_exists(empty_page):
    empty_page.add_log()
    assert "Add a connection first" in empty_page.status.text()
    assert empty_page.rows()[1] == []


# ------------------------------------------------------ formats: any backend
def test_the_format_picker_offers_shapes_aliases_and_custom(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    offered = {dialog.format.itemText(i) for i in range(dialog.format.count())}
    # Named after the shape of a line, not after one application...
    assert {"iso", "slash", "clf", "syslog", "none"} <= offered
    # ...with the names people actually look for as aliases...
    assert {"django", "fastapi", "node", "nginx", "apache", "odoo"} <= offered
    # ...and an escape hatch for anything else.
    assert lsf.CUSTOM_FORMAT in offered
    dialog.close()


def test_odoo_is_one_option_among_many_and_not_the_default(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    assert dialog.value().format == "iso"
    assert dialog.value().format != "odoo"
    dialog.close()


def test_a_preset_shows_an_example_of_the_line_it_reads(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.format.setCurrentText("syslog")
    assert "Aug 19" in dialog.format_hint.text()
    dialog.format.setCurrentText("django")        # an alias for the iso shape
    assert "2026-08-19" in dialog.format_hint.text()
    dialog.close()


def test_custom_reveals_the_pattern_fields_and_writes_them(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    assert not dialog.ts_regex_field.isVisibleTo(dialog)
    dialog.format.setCurrentText(lsf.CUSTOM_FORMAT)
    assert dialog.ts_regex_field.isVisibleTo(dialog)
    dialog.name.setText("weird")
    dialog.target.setText("/weird.log")
    dialog.envs.set_checked([LOCAL], notify=True)
    dialog.ts_regex.setText(r"^\[(\d{2}:\d{2}:\d{2})\]")
    dialog.ts_format.setText("%H:%M:%S")
    dialog.level_regex.setText(r"<(\w+)>")
    row = dialog.value()
    assert row.timestamp == {"regex": r"^\[(\d{2}:\d{2}:\d{2})\]",
                             "format": "%H:%M:%S"}
    assert row.level == {"regex": r"<(\w+)>"}
    assert row.is_custom and row.format_label() == lsf.CUSTOM_FORMAT
    dialog.close()


def test_the_timezone_applies_to_a_preset_not_only_to_custom(qapp):
    """The one setting that decides whether a preset matches anything at all.

    A backend logging UTC on a machine that is not put every line hours into the
    past, outside every session's window - and there was no way to say so without
    hand-writing a whole timestamp block.
    """
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.name.setText("app")
    dialog.target.setText("/var/log/app.log")
    dialog.envs.set_checked([LOCAL], notify=True)
    assert dialog.tz.isVisibleTo(dialog)          # with a preset selected
    dialog.tz.setCurrentText("utc")
    row = dialog.value()
    assert row.tz == "utc" and not row.is_custom  # still the preset it was
    assert row.to_entry()["tz"] == "utc"
    assert dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_a_local_timezone_is_not_written_out(qapp):
    # It is the default; writing it would only add noise to every log.
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.name.setText("app")
    dialog.target.setText("/x.log")
    assert "tz" not in dialog.value().to_entry()
    dialog.close()


def test_a_custom_pattern_that_does_not_compile_is_caught(qapp):
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.name.setText("weird")
    dialog.target.setText("/weird.log")
    dialog.envs.set_checked([LOCAL], notify=True)
    dialog.format.setCurrentText(lsf.CUSTOM_FORMAT)
    dialog.ts_regex.setText("([unclosed")
    assert "does not compile" in dialog.problem.text()
    assert not dialog.buttons.buttons()[0].isEnabled()
    dialog.close()


def test_a_custom_pattern_without_a_capturing_group_is_caught(qapp):
    # timestamp_of reads group(1); without one the whole match goes to strptime.
    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    dialog.name.setText("weird")
    dialog.target.setText("/weird.log")
    dialog.envs.set_checked([LOCAL], notify=True)
    dialog.format.setCurrentText(lsf.CUSTOM_FORMAT)
    dialog.ts_regex.setText(r"^\d{2}:\d{2}:\d{2}")
    assert "capture the value in a group" in dialog.problem.text()
    dialog.close()


def test_a_custom_format_survives_a_round_trip_through_the_editor(page, tmp_path):
    """The data-loss path this feature had: opening the editor destroyed it.

    ``timestamp`` and ``level`` are how any backend without a preset is read. They
    were parsed and then dropped on save, so one visit to this page silently
    replaced a hand-written custom format with whatever preset was in the combo.
    """
    custom = lsf.LogRow(name="weird", connection="here", envs=[LOCAL],
                        type="file", target="/weird.log",
                        timestamp={"regex": r"^\[(\d+)\]", "format": "%S"},
                        level={"regex": r"<(\w+)>"}, tz="utc")
    page._logs.append(custom)
    page.save()
    _connections_back, logs_back = lsf.load(page._path)
    kept = [row for row in logs_back if row.name == "weird"][0]
    assert kept.timestamp == {"regex": r"^\[(\d+)\]", "format": "%S"}
    assert kept.level == {"regex": r"<(\w+)>"}
    assert kept.tz == "utc"


def test_the_launcher_reads_every_format_this_page_offers(qapp):
    """The two format tables must not drift apart.

    A name the picker offers and the engine rejects means a saved file the next
    launch refuses - the exact failure the mirrored validation exists to prevent.
    """
    from engine import serverlog

    dialog = LogDialog(connections=_connections(), envs=[LOCAL])
    offered = [dialog.format.itemText(i) for i in range(dialog.format.count())]
    dialog.close()
    for name in offered:
        if not name or name == lsf.CUSTOM_FORMAT:
            continue                       # a separator, or the escape hatch
        assert serverlog.resolve_format(name) is not None, name


# ----------------------------------------------------------- rows, from the page
def test_renaming_a_connection_follows_through_to_its_logs(page, monkeypatch):
    # Logs point at a connection by name. Renaming one without this leaves every
    # log that used it pointing at something that no longer exists - which the
    # launcher then refuses the whole file for.
    def rename(dialog):
        dialog.name.setText("local-box")
        return True

    _accept(monkeypatch, rename)
    page.edit_connection(0)
    _connections, logs = page.rows()
    assert logs[0].connection == "local-box"
    assert page.save_button.isEnabled()


def test_a_duplicated_row_is_valid_the_moment_it_is_made(page):
    page.duplicate_log(0)
    _connections, logs = page.rows()
    assert [row.name for row in logs] == ["app", "app-copy", "nginx"]
    # A copy sharing its original's name in the same environment would be invalid
    # on arrival, which is a poor thing to hand somebody.
    assert page.save_button.isEnabled()


def test_deleting_a_connection_warns_about_the_logs_that_used_it(page, monkeypatch):
    asked = {}

    def question(_parent, _title, text, *_args, **_kwargs):
        from PySide6.QtWidgets import QMessageBox
        asked["text"] = text
        return QMessageBox.Yes

    monkeypatch.setattr("cms_gui.pages.logsources.QMessageBox.question", question)
    page.delete_connection(0)
    assert "1 log(s) use it: app" in asked["text"]
    assert not page.save_button.isEnabled()      # app now points nowhere


# ------------------------------------------------------------------- the file
def test_saving_writes_a_file_the_launcher_can_read(page):
    from engine import serverlog

    page.save()
    config = serverlog.load_config(page._path)
    assert [s.name for s in config.for_env(LOCAL)] == ["app"]
    assert [s.name for s in config.for_env(DEV)] == ["nginx"]


def test_saving_keeps_the_previous_file_as_a_backup(page):
    before = open(page._path, encoding="utf-8").read()
    page._logs[0].target = "/var/log/changed.log"
    page.save()
    assert open(page._path + ".bak", encoding="utf-8").read() == before
    assert "changed.log" in open(page._path, encoding="utf-8").read()


def test_a_broken_file_is_reported_rather_than_swallowed(qapp, tmp_path):
    path = tmp_path / "logsources.json"
    path.write_text("{ not json")
    widget = LogSourcesPage()
    widget.load(str(path))
    assert "not valid JSON" in widget.status.text()
    assert widget.rows() == ([], [])
    widget.close()


def test_a_file_that_could_not_be_read_cannot_be_saved_over(qapp, tmp_path):
    """The data-loss path: an empty editor over a file full of content.

    Validation calls the empty document perfectly valid, so without this Save
    stays lit and one click replaces a file whose only problem was a typo.
    """
    path = tmp_path / "logsources.json"
    path.write_text('{"connections": [ trailing comma, ]}')
    widget = LogSourcesPage()
    widget.load(str(path))
    assert not widget.save_button.isEnabled()
    assert "Reload" in widget.status.text()
    widget.close()


def test_reloading_a_fixed_file_clears_the_complaint(qapp, tmp_path):
    path = tmp_path / "logsources.json"
    path.write_text("{ not json")
    widget = LogSourcesPage()
    widget.load(str(path))
    path.write_text(json.dumps({"connections": [{"name": "here", "type": "local"}],
                                "logs": []}))
    widget.load(str(path))
    assert widget.save_button.isEnabled()
    assert "not valid JSON" not in widget.status.text()
    widget.close()


# ------------------------------------------------------- opening a log
class _Core:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def server_log_show(self, name, lines=None):
        self.asked.append((name, lines))
        return self.answer


def test_open_tail_asks_the_core_for_the_end_of_the_log(page):
    core = _Core({"ok": True, "target": "file (local) /x",
                  "lines": ["one", "two"], "truncated": True})
    page.set_core(core)
    page.logs.setCurrentCell(0, page.L_NAME)
    page.open_selected_log(whole=False)
    assert core.asked == [("app", 500)]
    assert "2 line(s)" in page.status.text()


def test_open_full_asks_for_all_of_it(page):
    core = _Core({"ok": True, "target": "file (local) /x", "lines": ["one"]})
    page.set_core(core)
    page.logs.setCurrentCell(0, page.L_NAME)
    page.open_selected_log(whole=True)
    # None is "as much as the core's byte budget allows" - the GUI does not
    # invent a number the core would have to second-guess.
    assert core.asked == [("app", None)]


def test_a_failure_shows_what_the_launcher_said(page):
    page.set_core(_Core({"ok": False,
                         "error": "ssh: Could not resolve hostname nx.invalid"}))
    page.logs.setCurrentCell(0, page.L_NAME)
    page.open_selected_log(whole=False)
    assert "Could not resolve hostname" in page.status.text()


def test_opening_unsaved_edits_says_to_save_first(page):
    # The launcher reads the file, not the screen.
    page.set_core(_Core({"ok": True, "lines": []}))
    page._fingerprint = ("stale",)
    page.logs.setCurrentCell(0, page.L_NAME)
    page.open_selected_log(whole=False)
    assert "Save first" in page.status.text()


def test_opening_an_invalid_file_says_to_fix_it_first(page):
    page.set_core(_Core({"ok": True, "lines": []}))
    page._logs[0].connection = "gone"
    page.logs.setCurrentCell(0, page.L_NAME)
    page.open_selected_log(whole=False)
    assert "Fix the problems" in page.status.text()


def test_opening_with_nothing_selected_says_so(page):
    page.set_core(_Core({"ok": True, "lines": []}))
    page.logs.setCurrentCell(-1, -1)
    page.open_selected_log(whole=False)
    assert "Select a log" in page.status.text()


def test_the_save_button_does_not_spell_out_the_file_name(page):
    assert page.save_button.text() == "Save"


# ------------------------------------------------------------------ the viewer
def _viewer(qapp, **result):
    result.setdefault("lines", ["alpha", "beta", "gamma"])
    result.setdefault("target", "file (local) /x")
    return LogViewerDialog("app", result, whole=False)


def test_the_viewer_shows_every_line_it_was_given(qapp):
    viewer = _viewer(qapp)
    assert viewer.view.toPlainText().splitlines() == ["alpha", "beta", "gamma"]
    assert "3 of 3 lines" in viewer.count.text()
    viewer.close()


def test_the_viewer_filters_without_losing_the_total(qapp):
    viewer = _viewer(qapp)
    viewer.filter.setText("a")
    assert viewer.view.toPlainText().splitlines() == ["alpha", "beta", "gamma"]
    viewer.filter.setText("alph")
    assert viewer.view.toPlainText().splitlines() == ["alpha"]
    assert "1 of 3 lines" in viewer.count.text()
    viewer.close()


def test_a_truncated_read_says_it_starts_mid_log(qapp):
    # Otherwise the first line shown reads as the first line there is.
    viewer = _viewer(qapp, truncated=True)
    assert "end of the log" in viewer.note.text()
    viewer.close()


def test_an_empty_log_is_not_reported_as_a_fault(qapp):
    viewer = _viewer(qapp, lines=[], empty=True)
    assert "not a fault" in viewer.note.text()
    viewer.close()


def _accept(monkeypatch, fill):
    """Make the next dialog open, run ``fill``, and accept without an event loop."""
    from PySide6.QtWidgets import QDialog

    def exec_(self):
        return QDialog.Accepted if fill(self) else QDialog.Rejected

    monkeypatch.setattr(ConnectionDialog, "exec", exec_, raising=False)
    monkeypatch.setattr(LogDialog, "exec", exec_, raising=False)
