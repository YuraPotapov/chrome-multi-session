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
