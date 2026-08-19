"""The execution record: written once, read back after a restart, and capped.

A history entry has to survive the process that wrote it - that is the whole
point of it - so these tests always read it back through a second ``History``
over the same directory rather than trusting the in-memory list.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui import history as history_mod, store


def _history(tmp_path, qapp):
    return history_mod.History(directory=str(tmp_path))


def _reopened(tmp_path):
    """What a fresh session would see."""
    return history_mod.History(directory=str(tmp_path))


# ------------------------------------------------------------------ round trips

def test_a_launch_entry_keeps_both_the_configuration_and_the_command(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    entry_id = history.begin(history_mod.LAUNCH, {
        "argv": ["python3", "session_launcher.py", "--env=localhost:8069"],
        "display_command": "session_launcher.py --env=localhost:8069",
        "summary": "localhost:8069 · admin",
        "launch_config": {"environment": "localhost:8069"},
        "command_state": {"--env": "localhost:8069"}})
    history.finish(entry_id, status=history_mod.OK, exit_code=0, passed=3, total=3,
                   run_dir=str(tmp_path / "reports" / "run"))

    entry = _reopened(tmp_path).entry(entry_id)
    assert entry["kind"] == history_mod.LAUNCH
    assert entry["status"] == history_mod.OK
    assert entry["launch_config"]["environment"] == "localhost:8069"
    # The generated command is stored too: an entry has to be readable, and
    # re-runnable, without the page that produced it.
    assert entry["command_state"]["--env"] == "localhost:8069"
    assert entry["passed"] == 3 and entry["total"] == 3


def test_a_command_entry_keeps_the_form_it_was_submitted_from(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    state = {"--env": "localhost:8069", "--run-tests": "all", "--detach": True}
    entry_id = history.begin(history_mod.COMMAND, {"command_state": state,
                                                   "argv": ["--run-tests=all"]})
    history.finish(entry_id, status=history_mod.FAILED, exit_code=1)

    entry = _reopened(tmp_path).entry(entry_id)
    assert entry["kind"] == history_mod.COMMAND
    assert entry["command_state"] == state
    assert "launch_config" not in entry


def test_an_entry_starts_out_marked_as_running(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    entry_id = history.begin(history_mod.LAUNCH, {})
    # Already on disk before the run has produced anything: a GUI killed
    # mid-run leaves the entry saying "running", which is the honest answer.
    assert _reopened(tmp_path).entry(entry_id)["status"] == history_mod.RUNNING


def test_entries_come_back_newest_first_and_can_be_filtered_by_kind(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    first = history.begin(history_mod.COMMAND, {})
    second = history.begin(history_mod.LAUNCH, {})
    assert [e["id"] for e in history.entries()] == [second, first]
    assert [e["id"] for e in history.entries(history_mod.LAUNCH)] == [second]
    assert [e["id"] for e in history.entries(history_mod.COMMAND)] == [first]


def test_finishing_an_unknown_entry_is_ignored(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    assert history.finish("no-such-entry", status=history_mod.OK) is None


def test_a_duration_is_worked_out_when_one_is_not_supplied(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    entry_id = history.begin(history_mod.LAUNCH, {"started_at": "2026-08-13 10:00:00"})
    entry = history.finish(entry_id, status=history_mod.OK,
                           finished_at="2026-08-13 10:02:30")
    assert entry["duration_ms"] == 150_000


# ------------------------------------------------------------------ housekeeping

def test_the_cap_drops_the_oldest_entry_and_the_log_it_owned(tmp_path, qapp, monkeypatch):
    monkeypatch.setattr(history_mod, "MAX_ENTRIES", 3)
    history = _history(tmp_path, qapp)
    logs = []
    for index in range(4):
        entry_id = history.begin(history_mod.LAUNCH, {})
        path = history.log_path(entry_id)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("run %d\n" % index)
        history.finish(entry_id, status=history_mod.OK, log_file=path)
        logs.append(path)

    assert len(history.entries()) == 3
    # The evicted entry's archived log goes with it - nothing else keeps a
    # reference to it, so leaving it behind would just grow forever.
    assert not os.path.exists(logs[0])
    assert all(os.path.exists(p) for p in logs[1:])


def test_deleting_an_entry_removes_its_log_but_never_a_file_the_user_chose(
        tmp_path, qapp):
    history = _history(tmp_path, qapp)
    mine = tmp_path / "somewhere-else.log"
    mine.write_text("saved by hand\n", encoding="utf-8")

    archived_id = history.begin(history_mod.LAUNCH, {})
    archived = history.log_path(archived_id)
    with open(archived, "w", encoding="utf-8") as handle:
        handle.write("x\n")
    history.finish(archived_id, status=history_mod.OK, log_file=archived)

    external_id = history.begin(history_mod.LAUNCH, {})
    history.finish(external_id, status=history_mod.OK, log_file=str(mine))

    history.remove(archived_id)
    history.remove(external_id)
    assert not os.path.exists(archived)
    assert mine.exists(), "a log the user saved themselves is not ours to delete"


def test_clearing_empties_the_record(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    history.begin(history_mod.LAUNCH, {})
    history.begin(history_mod.COMMAND, {})
    history.clear()
    assert history.entries() == []
    assert _reopened(tmp_path).entries() == []


def test_ids_stay_unique_even_within_the_same_second(tmp_path, qapp):
    history = _history(tmp_path, qapp)
    ids = [history.begin(history_mod.LAUNCH, {}) for _ in range(5)]
    assert len(set(ids)) == 5


# ------------------------------------------------------------------ bad input

def test_a_corrupt_history_file_degrades_to_an_empty_record(tmp_path, qapp):
    (tmp_path / "history.json").write_text("{not json at all", encoding="utf-8")
    history = _history(tmp_path, qapp)
    assert history.entries() == []
    # And it recovers: the next run is recorded normally.
    history.begin(history_mod.LAUNCH, {})
    assert len(_reopened(tmp_path).entries()) == 1


def test_a_history_file_of_the_wrong_shape_is_ignored(tmp_path, qapp):
    (tmp_path / "history.json").write_text(json.dumps({"entries": []}),
                                           encoding="utf-8")
    assert _history(tmp_path, qapp).entries() == []


def test_non_dict_rows_are_skipped_rather_than_crashing_the_page(tmp_path, qapp):
    (tmp_path / "history.json").write_text(
        json.dumps([{"id": "a", "kind": "launch"}, "junk", 7]), encoding="utf-8")
    entries = _history(tmp_path, qapp).entries()
    assert [e["id"] for e in entries] == ["a"]


# ------------------------------------------------------------------ presentation

def test_a_status_is_derived_from_how_the_run_actually_ended():
    assert history_mod.status_for(0) == history_mod.OK
    assert history_mod.status_for(1) == history_mod.FAILED
    assert history_mod.status_for(1, stopped=True) == history_mod.STOPPED
    # Stopping on purpose is not a failure, even though the exit code says so.
    assert history_mod.status_for(0, stopped=True) == history_mod.STOPPED
    assert history_mod.status_for(None, failed_to_start=True) == history_mod.ERROR


def test_durations_read_as_a_person_would_say_them():
    assert history_mod.format_duration(None) == ""
    assert history_mod.format_duration(400) == "0.4s"
    assert history_mod.format_duration(12_000) == "12s"
    assert history_mod.format_duration(187_000) == "3m 07s"
    assert history_mod.format_duration(3_840_000) == "1h 04m"


# ------------------------------------------------------------------ the store

def test_saved_configurations_survive_a_restart(tmp_path):
    path = str(tmp_path / "configs.json")
    configs = store.NamedConfigs(path)
    configs.put("Smoke, localhost", {"environment": "localhost:8069"})
    assert store.NamedConfigs(path).get("Smoke, localhost") == {
        "environment": "localhost:8069"}


def test_a_configuration_can_be_duplicated_renamed_and_deleted(tmp_path):
    path = str(tmp_path / "configs.json")
    configs = store.NamedConfigs(path)
    configs.put("Nightly", {"environment": "a"})
    assert configs.unique_name("Nightly") == "Nightly (2)"
    configs.put(configs.unique_name("Nightly"), {"environment": "b"})
    assert configs.names() == ["Nightly", "Nightly (2)"]
    configs.rename("Nightly (2)", "Weekly")
    assert configs.names() == ["Nightly", "Weekly"]
    configs.remove("Nightly")
    assert store.NamedConfigs(path).names() == ["Weekly"]


def test_a_returned_configuration_is_a_copy(tmp_path):
    configs = store.NamedConfigs(str(tmp_path / "configs.json"))
    configs.put("One", {"users": {"logins": ["admin"]}})
    fetched = configs.get("One")
    fetched["users"]["logins"].append("intruder")
    assert configs.get("One")["users"]["logins"] == ["admin"]


def test_a_corrupt_configuration_file_degrades_to_no_configurations(tmp_path):
    path = tmp_path / "configs.json"
    path.write_text("]]not json", encoding="utf-8")
    assert store.NamedConfigs(str(path)).names() == []


# ------------------------------------------- keeping what someone saved

def test_a_file_with_a_byte_order_mark_still_reads(tmp_path):
    """Every configuration read back as none at all, from three bytes.

    PowerShell's "utf8" writes a \ufeff, json.load refuses one, and the store
    answered with its empty default - an empty picker over a full file.
    """
    path = tmp_path / "configs.json"
    path.write_text('\ufeff{"Weekly": {"env": "dev"}}', encoding="utf-8")
    assert store.NamedConfigs(str(path)).names() == ["Weekly"]


def test_an_unreadable_file_is_kept_rather_than_overwritten(tmp_path):
    # The file that cannot be parsed is still the user's data. Saving beside it
    # must not be the moment it disappears.
    path = tmp_path / "configs.json"
    path.write_text('{"Weekly": {"env": "dev"', encoding="utf-8")   # truncated
    configs = store.NamedConfigs(str(path))
    assert configs.names() == []            # nothing readable, so nothing shown
    configs.put("New", {"env": "stg"})
    kept = list(tmp_path.glob("configs.json.unreadable-*"))
    assert kept, "the unreadable file was overwritten without a copy"
    assert "Weekly" in kept[0].read_text(encoding="utf-8")
    assert store.NamedConfigs(str(path)).names() == ["New"]


def test_a_save_does_not_erase_what_another_window_saved(tmp_path):
    """Two windows are two copies of this object, each with its own snapshot.

    The one that saves last used to write the list it read at startup, and the
    other window's configuration was gone.
    """
    path = str(tmp_path / "configs.json")
    first, second = store.NamedConfigs(path), store.NamedConfigs(path)
    first.put("From the first window", {"env": "dev"})
    second.put("From the second window", {"env": "stg"})
    assert store.NamedConfigs(path).names() == [
        "From the first window", "From the second window"]


def test_a_delete_in_one_window_leaves_the_rest_alone(tmp_path):
    path = str(tmp_path / "configs.json")
    configs = store.NamedConfigs(path)
    configs.put("Keep", {"env": "dev"})
    configs.put("Drop", {"env": "stg"})
    store.NamedConfigs(path).remove("Drop")
    assert store.NamedConfigs(path).names() == ["Keep"]
