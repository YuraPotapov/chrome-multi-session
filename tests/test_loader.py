import os

import pytest

from engine import loader

# The engine's OWN fixture flows, not whatever app tree happens to sit in ../flows.
# Keeps these tests about the loader; see tests/fixtures/flows/selectors.yaml.
FLOWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "flows")


def test_flow_path_dotted_is_a_path():
    assert loader.flow_path("auth.login", FLOWS).endswith(
        os.path.join("auth", "login.yaml"))


def test_flow_path_bare_id_is_a_scenario():
    assert loader.flow_path("demo_smoke", FLOWS).endswith(
        os.path.join("scenarios", "demo_smoke.yaml"))


def test_load_flow_reads_steps():
    flow = loader.load_flow("auth.login", FLOWS)
    assert flow.id == "auth.login"
    assert isinstance(flow.steps, list) and flow.steps


def test_load_flow_missing_raises():
    with pytest.raises(loader.FlowNotFound):
        loader.load_flow("no.such.flow", FLOWS)


def test_discover_skips_templates_and_manual():
    ids = loader.discover_scenarios(FLOWS)
    assert "demo_smoke" in ids
    assert "smoke_reclamation" not in ids   # tagged `template`
    assert "smoke_contacts" not in ids
    assert "create_reclamation" not in ids  # tagged `manual` (writes data)


def test_discover_can_include_skipped():
    ids = loader.discover_scenarios(FLOWS, include_templates=True)
    assert "demo_manual" in ids          # skipped by `all`, but listed when asked for


def test_load_selectors():
    selectors = loader.load_selectors(FLOWS)
    assert selectors.get("dashboard") == ".app-root"


def test_scenarios_with_tag():
    manual = loader.scenarios_with_tag("manual", FLOWS)
    assert "demo_manual" in manual        # tagged manual (listed even though `all` skips it)
    assert "demo_smoke" not in manual     # not tagged manual
    simple = loader.scenarios_with_tag("simple", FLOWS)
    assert "demo_simple" in simple
    assert "demo_smoke" not in simple


# ------------------------------------------------------------- layered trees
# An installed build keeps its flows inside the application bundle, where nothing
# can be written, so anything the user creates lives in a tree of their own that
# is searched first. These are the properties that makes usable.

@pytest.fixture
def layered(tmp_path, monkeypatch):
    """A user tree in front of the fixture tree, as an installed build has."""
    user = tmp_path / "user-flows"
    (user / "scenarios").mkdir(parents=True)
    monkeypatch.setattr(loader.runtime_paths, "flows_search_path",
                        lambda: [str(user), FLOWS])
    return user


def _scenario(path, flow_id, name, tags=()):
    path.write_text("id: %s\nname: %s\ntags: [%s]\nsteps:\n  - assert_visible: dashboard\n"
                    % (flow_id, name, ", ".join(tags)), encoding="utf-8")


def test_a_user_scenario_is_found(layered):
    _scenario(layered / "scenarios" / "recorded.yaml", "recorded", "Recorded")
    assert "recorded" in loader.discover_scenarios()
    assert loader.load_flow("recorded").name == "Recorded"


def test_a_user_scenario_shadows_a_bundled_one_of_the_same_id(layered):
    # One id names one file. Otherwise --run-tests=all would find the pair and
    # they would disagree about what it should run.
    _scenario(layered / "scenarios" / "demo_smoke.yaml", "demo_smoke", "Mine")
    assert loader.load_flow("demo_smoke").name == "Mine"
    assert loader.discover_scenarios().count("demo_smoke") == 1


def test_a_user_scenario_still_reaches_the_bundled_blocks(layered):
    # The whole point of searching rather than copying: a recorded scenario can
    # `use: auth.login` without the user's tree holding a copy of it.
    assert loader.load_flow("auth.login").steps
    assert loader.flow_path("auth.login").startswith(FLOWS)


def test_selectors_merge_with_the_users_taking_precedence(layered):
    (layered / "selectors.yaml").write_text("dashboard: \".mine\"\nextra: \"#x\"\n",
                                            encoding="utf-8")
    selectors = loader.load_selectors()
    assert selectors["dashboard"] == ".mine"      # overridden
    assert selectors["extra"] == "#x"             # added
    assert selectors["user_menu"] == ".app-user-menu"   # bundled ones still resolve


def test_an_explicit_flows_dir_means_only_that_directory(layered):
    # --flows-dir is taken literally; only the default is layered. Otherwise
    # pointing at a tree would silently pull in flows from somewhere else.
    _scenario(layered / "scenarios" / "recorded.yaml", "recorded", "Recorded")
    assert "recorded" not in loader.discover_scenarios(FLOWS)
    with pytest.raises(loader.FlowNotFound):
        loader.load_flow("recorded", FLOWS)


def test_a_missing_flow_points_at_where_it_would_be_written(layered):
    # The nearest tree, not the read-only bundle - that is where it would go.
    assert loader.canonical_path("brand_new").startswith(str(layered))
