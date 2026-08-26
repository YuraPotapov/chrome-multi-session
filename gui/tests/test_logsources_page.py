"""The forms behind logsources.json: connections, logs, and reading one.

A row is created and edited in a dialog, not in a grid, so these drive
:class:`ConnectionDialog` / :class:`LogDialog` directly - that is where the fields
live, and where a half-filled row is caught. What the page around them does with
the file is in ``test_services_page.py``, because that page owns two files and
these dialogs only describe one.
"""

import json
import os
import sys

import pytest

from cms_gui import logsourcesfile as lsf
from cms_gui.pages.logsources import (ConnectionDialog, LogDialog,
                                      LogViewerDialog)

# The core is one directory up and not installed. The GUI never imports it at
# runtime; a test may, to check the two agree about the file.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

LOCAL = "localhost:8069"
DEV = "https://dev.example.com/"


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


# ------------------------------------------------------- the form's own height
def test_a_form_does_not_open_taller_than_what_is_in_it(qapp):
    """Every hint under a field is a word-wrapped QLabel, and one of those
    reports a sizeHint for a width it has not been given - always more lines than
    it will take. Adding those up stood a band of nothing under the last field.
    """
    import time

    dialog = ConnectionDialog()
    dialog.show()
    for _ in range(30):                     # let the one-shot settle run
        qapp.processEvents()
        time.sleep(0.01)

    content = 0
    for index in range(dialog.column.count()):
        widget = dialog.column.itemAt(index).widget()
        if widget is not None and widget.isVisible():
            content = max(content, widget.geometry().bottom() + 1)
    slack = dialog._body.height() - (content
                                     + dialog.column.contentsMargins().bottom())
    assert slack <= 2, "%dpx of nothing under the last field" % slack
    dialog.close()


def test_a_form_taller_than_the_screen_still_scrolls(qapp):
    # At the cap the body is already scrolling and there is no slack to take;
    # shrinking further would put the buttons out of reach.
    dialog = LogDialog(connections=[lsf.ConnectionRow(name="here")], envs=[LOCAL])
    dialog.show()
    qapp.processEvents()
    before = dialog.height()
    dialog._settle()
    assert dialog.height() <= before
    assert dialog.buttons.isVisible()
    dialog.close()
