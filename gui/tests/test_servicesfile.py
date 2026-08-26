"""cms_gui.servicesfile: the projects and their runners on disk.

The contract: what the editor writes, the editor reads back unchanged - including
the keys it has never heard of. A settings dict belongs to its runner type and is
passed through untouched, so a type that grows a field must not need this module
to know about it.
"""

import json

import pytest

from cms_gui import servicesfile as sf


def _rows():
    return [sf.ProjectRow(name="Claim", dir="/srv/claim", runners=[
        sf.RunnerRow(name="Odoo Local", type="python",
                     settings={"script": "odoo-bin", "args": "-c odoo.conf",
                               "env": {"PYTHONUNBUFFERED": "1"}}),
        sf.RunnerRow(name="PostgreSQL DB", type="docker",
                     settings={"container": "postgres-claim"})])]


# ------------------------------------------------------------------ round trip
def test_what_is_written_comes_back_the_same(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), _rows())
    projects = sf.load(str(path))
    assert [p.name for p in projects] == ["Claim"]
    assert projects[0].dir == "/srv/claim"
    assert [r.name for r in projects[0].runners] == ["Odoo Local", "PostgreSQL DB"]
    assert projects[0].runners[0].settings["env"] == {"PYTHONUNBUFFERED": "1"}


def test_a_missing_file_is_nothing_configured_not_an_error(tmp_path):
    assert sf.load(str(tmp_path / "nothing.json")) == []


def test_keys_this_module_never_heard_of_survive_the_editor(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"projects": [
        {"name": "Claim", "colour": "blue", "runners": [
            {"name": "x", "type": "shell", "settings": {"command": "true"},
             "note": "written by hand"}]}]}))
    sf.save(str(path), sf.load(str(path)))
    written = json.loads(path.read_text())
    assert written["projects"][0]["colour"] == "blue"
    assert written["projects"][0]["runners"][0]["note"] == "written by hand"


def test_a_settings_dict_is_passed_through_rather_than_understood(tmp_path):
    path = tmp_path / "services.json"
    row = sf.RunnerRow(name="x", type="shell",
                       settings={"command": "true", "invented-later": 7})
    sf.save(str(path), [sf.ProjectRow(name="P", runners=[row])])
    back = sf.load(str(path))[0].runners[0]
    assert back.settings["invented-later"] == 7


def test_a_stack_remembers_whether_its_block_was_open(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), [sf.ProjectRow(name="P", expanded=False)])
    assert sf.load(str(path))[0].expanded is False


def test_a_stack_written_by_hand_opens_rather_than_hides(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"projects": [{"name": "P"}]}))
    assert sf.load(str(path))[0].expanded is True


# ------------------------------------------------------------------- detach
def test_a_new_runner_takes_its_kinds_answer_about_detaching():
    assert sf.RunnerRow(name="a", type="python").detach is False
    # A container is the daemon's, so it is detached whether anyone said so.
    assert sf.RunnerRow(name="b", type="docker").detach is True


def test_an_explicit_answer_is_kept_over_the_kinds_default():
    assert sf.RunnerRow(name="a", type="python", detach=True).detach is True


# ---------------------------------------------------------------- validation
def test_a_stack_needs_a_name():
    assert "a name is required" in " ".join(sf.validate([sf.ProjectRow()]))


def test_two_stacks_cannot_share_a_name():
    problems = sf.validate([sf.ProjectRow(name="A"), sf.ProjectRow(name="A")])
    assert problems and "must be unique" in problems[0]


def test_two_services_in_one_stack_cannot_share_a_name():
    project = sf.ProjectRow(name="A", runners=[
        sf.RunnerRow(name="x", type="shell", settings={"command": "true"}),
        sf.RunnerRow(name="x", type="shell", settings={"command": "true"})])
    assert "must be unique within a project" in " ".join(sf.validate([project]))


def test_the_same_name_in_two_stacks_is_fine():
    def stack(name):
        return sf.ProjectRow(name=name, runners=[
            sf.RunnerRow(name="db", type="shell", settings={"command": "true"})])
    assert sf.validate([stack("A"), stack("B")]) == []


def test_a_type_this_build_does_not_have_is_reported_not_dropped():
    project = sf.ProjectRow(name="A", runners=[sf.RunnerRow(name="x", type="quantum")])
    problems = sf.validate([project])
    assert problems and "unknown type" in problems[0]


def test_the_type_is_asked_about_its_own_fields():
    project = sf.ProjectRow(name="A", runners=[sf.RunnerRow(name="x", type="python")])
    assert "Script is required" in " ".join(sf.validate([project]))


def test_a_stack_may_point_at_a_directory_that_does_not_exist_yet():
    # A stack can be configured before the checkout is cloned.
    assert sf.validate([sf.ProjectRow(name="A", dir="/not/here/yet")]) == []


def test_a_stack_pointed_at_a_file_never_becomes_right(tmp_path):
    target = tmp_path / "a-file"
    target.write_text("")
    problems = sf.validate([sf.ProjectRow(name="A", dir=str(target))])
    assert problems and "is a file" in problems[0]


def test_saving_an_invalid_document_raises_rather_than_writing_it(tmp_path):
    path = tmp_path / "services.json"
    with pytest.raises(sf.ServicesFileError):
        sf.save(str(path), [sf.ProjectRow(name="")])
    assert not path.exists()


# ---------------------------------------------------------------- the writing
def test_the_previous_file_is_kept_as_a_backup(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), [sf.ProjectRow(name="First")])
    sf.save(str(path), [sf.ProjectRow(name="Second")])
    assert json.loads((tmp_path / "services.json.bak").read_text())[
        "projects"][0]["name"] == "First"


def test_no_temp_file_is_left_behind(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), _rows())
    assert [p.name for p in tmp_path.iterdir()] == ["services.json"]


def test_a_byte_order_mark_does_not_read_as_content(tmp_path):
    # A file written by a Windows shell carries one, and strict utf-8 chokes.
    path = tmp_path / "services.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"projects": [{"name": "Claim"}]}).encode("utf-8"))
    assert [p.name for p in sf.load(str(path))] == ["Claim"]


def test_a_file_that_is_not_json_says_so(tmp_path):
    path = tmp_path / "services.json"
    path.write_text("{not json")
    with pytest.raises(sf.ServicesFileError):
        sf.load(str(path))


def test_projects_must_be_an_array(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"projects": {"Claim": {}}}))
    with pytest.raises(sf.ServicesFileError):
        sf.load(str(path))


# --------------------------------------------------------------- fingerprints
def test_a_change_on_disk_is_noticed(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), [sf.ProjectRow(name="A")])
    before = sf.fingerprint(str(path))
    sf.save(str(path), [sf.ProjectRow(name="A"), sf.ProjectRow(name="B")])
    assert sf.fingerprint(str(path)) != before


def test_a_missing_file_has_no_fingerprint(tmp_path):
    assert sf.fingerprint(str(tmp_path / "gone.json")) is None


# ----------------------------------------------------------------- the path
def test_services_json_is_the_sibling_of_the_file_the_core_names():
    assert sf.path_beside("/data/cms/logsources.json") == "/data/cms/services.json"
    assert sf.path_beside("") == ""


# --------------------------------------------------------------------- copies
def test_a_copied_runner_shares_nothing_with_the_original():
    row = sf.RunnerRow(name="a", type="shell", settings={"command": "true"})
    clone = row.copy()
    clone.settings["command"] = "false"
    assert row.settings["command"] == "true"


def test_a_copied_project_copies_its_runners_too():
    project = _rows()[0]
    clone = project.copy()
    clone.runners[0].name = "renamed"
    assert project.runners[0].name == "Odoo Local"


# ------------------------------------------------------------- dependencies
def _svc(name, depends=()):
    return sf.RunnerRow(name=name, type="shell", settings={"command": "true"},
                        depends=list(depends))


def test_what_a_service_waits_for_survives_a_round_trip(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), [sf.ProjectRow(name="P", runners=[
        _svc("db"), _svc("web", ["db"])])])
    assert sf.load(str(path))[0].runner("web").depends == ["db"]


def test_a_service_that_waits_for_nothing_writes_no_key(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), [sf.ProjectRow(name="P", runners=[_svc("db")])])
    written = json.loads(path.read_text())
    assert "depends" not in written["projects"][0]["runners"][0]


def test_one_name_is_taken_as_a_list_of_one(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"projects": [{"name": "P", "runners": [
        {"name": "web", "type": "shell", "settings": {"command": "true"},
         "depends": "db"}]}]}))
    assert sf.load(str(path))[0].runner("web").depends == ["db"]


def test_what_has_to_be_up_first_is_ordered_first():
    # Listed web-then-db, started db-then-web: the order in the file is the order
    # they were typed in, not an instruction.
    project = sf.ProjectRow(name="P", runners=[_svc("web", ["db"]), _svc("db")])
    assert [r.name for r in project.start_order()] == ["db", "web"]


def test_services_that_wait_for_nothing_keep_the_order_they_were_written_in():
    project = sf.ProjectRow(name="P", runners=[_svc("one"), _svc("two"), _svc("three")])
    assert [r.name for r in project.start_order()] == ["one", "two", "three"]


def test_a_chain_is_walked_all_the_way_down():
    project = sf.ProjectRow(name="P", runners=[
        _svc("web", ["cache"]), _svc("cache", ["db"]), _svc("db")])
    assert [r.name for r in project.start_order()] == ["db", "cache", "web"]


def test_everything_downstream_of_a_service_can_be_found():
    project = sf.ProjectRow(name="P", runners=[
        _svc("db"), _svc("cache", ["db"]), _svc("web", ["cache"]), _svc("other")])
    assert sorted(r.name for r in project.needed_by("db")) == ["cache", "web"]


def test_waiting_for_something_that_is_not_there_is_caught():
    project = sf.ProjectRow(name="P", runners=[_svc("web", ["ghost"])])
    problems = sf.validate([project])
    assert problems and "not a service in this project" in problems[0]


def test_a_service_cannot_wait_for_itself():
    problems = sf.validate([sf.ProjectRow(name="P", runners=[_svc("a", ["a"])])])
    assert [p for p in problems if "cannot wait for itself" in p]
    # And only once: the loop check must not say the same thing again in worse words.
    assert len(problems) == 1


def test_a_ring_of_services_is_refused_and_named():
    project = sf.ProjectRow(name="P", runners=[
        _svc("a", ["b"]), _svc("b", ["c"]), _svc("c", ["a"])])
    problems = sf.validate([project])
    assert problems and "in a loop" in problems[0]
    assert "a -> b -> c -> a" in problems[0]


def test_ordering_a_ring_stops_rather_than_recursing_forever():
    # validate() refuses it, but a file edited by hand reaches start_order first.
    project = sf.ProjectRow(name="P", runners=[
        _svc("a", ["b"]), _svc("b", ["c"]), _svc("c", ["a"])])
    assert len(project.start_order()) == 3


# ---------------------------------------------------------------- criteria
from cms_gui import criteria as cr                                  # noqa: E402


def _watcher(name="start", **kwargs):
    kwargs.setdefault("rules", [cr.Rule(cr.MATCH, cr.TEXT, "up")])
    return cr.CriterionRow(name=name, **kwargs)


def test_what_a_service_watches_for_survives_a_round_trip(tmp_path):
    path = tmp_path / "services.json"
    runner = sf.RunnerRow(name="odoo", type="shell", settings={"command": "true"},
                          criteria=[_watcher(color="blue", source="/x/odoo.log")])
    sf.save(str(path), [sf.ProjectRow(name="P", runners=[runner])])
    back = sf.load(str(path))[0].runners[0].criteria[0]
    assert (back.name, back.color, back.source) == ("start", "blue", "/x/odoo.log")
    assert back.rules[0].pattern == "up"


def test_a_service_that_watches_nothing_writes_no_key(tmp_path):
    path = tmp_path / "services.json"
    sf.save(str(path), [sf.ProjectRow(name="P", runners=[_svc("plain")])])
    written = json.loads(path.read_text())
    assert "criteria" not in written["projects"][0]["runners"][0]


def test_criteria_are_validated_through_their_own_module(tmp_path):
    broken = _watcher(rules=[cr.Rule(cr.MATCH, cr.REGEX, "a(")])
    project = sf.ProjectRow(name="P", runners=[
        sf.RunnerRow(name="odoo", type="shell", settings={"command": "true"},
                     criteria=[broken])])
    problems = sf.validate([project])
    assert problems and "does not compile" in problems[0]
    # And it says which criterion, on which service, in which project.
    assert "criterion 1 (start)" in problems[0] and "odoo" in problems[0]


def test_two_criteria_on_one_service_cannot_share_a_name():
    project = sf.ProjectRow(name="P", runners=[
        sf.RunnerRow(name="odoo", type="shell", settings={"command": "true"},
                     criteria=[_watcher(), _watcher()])])
    assert "must be unique within a service" in " ".join(sf.validate([project]))


def test_the_same_criterion_name_on_two_services_is_fine():
    def service(name):
        return sf.RunnerRow(name=name, type="shell", settings={"command": "true"},
                            criteria=[_watcher()])
    project = sf.ProjectRow(name="P", runners=[service("a"), service("b")])
    assert sf.validate([project]) == []


def test_criteria_that_are_not_an_array_are_refused(tmp_path):
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"projects": [{"name": "P", "runners": [
        {"name": "a", "type": "shell", "settings": {"command": "true"},
         "criteria": "not an array"}]}]}))
    with pytest.raises(sf.ServicesFileError):
        sf.load(str(path))


def test_a_copied_service_copies_what_it_watches_for():
    runner = sf.RunnerRow(name="odoo", type="shell", settings={"command": "true"},
                          criteria=[_watcher()])
    clone = runner.copy()
    clone.criteria[0].name = "renamed"
    assert runner.criteria[0].name == "start"


# --------------------------------------------------------------- where it lives
def test_the_default_is_the_users_own_directory_not_a_checkout(monkeypatch,
                                                               tmp_path):
    """From a source checkout the launcher's config path *is* the checkout, so
    a services.json beside it landed in somebody's repository."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sf.default_path() == str(tmp_path / sf.USER_DIR_NAME / "services.json")


def test_what_settings_says_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    chosen = str(tmp_path / "elsewhere" / "svc.json")
    read, write = sf.resolve_path(chosen, "/repo/logsources.json")
    assert read == write == chosen


def test_a_tilde_in_the_setting_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _read, write = sf.resolve_path("~/mine.json", "")
    assert write == str(tmp_path / "mine.json")


def test_the_old_location_is_still_read_when_the_new_one_is_empty(monkeypatch,
                                                                  tmp_path):
    # An upgrade must not look like the projects were lost.
    monkeypatch.setenv("HOME", str(tmp_path))
    checkout = tmp_path / "repo"
    checkout.mkdir()
    legacy = checkout / "services.json"
    legacy.write_text("{}")
    read, write = sf.resolve_path("", str(checkout / "logsources.json"))
    assert read == str(legacy)
    assert write == sf.default_path()


def test_once_the_new_one_exists_the_old_one_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    checkout = tmp_path / "repo"
    checkout.mkdir()
    (checkout / "services.json").write_text("{}")
    target = tmp_path / sf.USER_DIR_NAME / "services.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    read, write = sf.resolve_path("", str(checkout / "logsources.json"))
    assert read == write == str(target)


def test_with_nothing_anywhere_it_reads_and_writes_the_default(monkeypatch,
                                                               tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    read, write = sf.resolve_path("", "/repo/logsources.json")
    assert read == write == sf.default_path()
