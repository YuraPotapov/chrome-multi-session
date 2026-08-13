"""Load declarative flows/blocks/scenarios from the ``flows/`` tree.

Flow ids map to files by a simple convention:
  - dotted ids are paths:  ``auth.login`` -> ``flows/auth/login.yaml``
  - bare ids are scenarios: ``my_scenario`` -> ``flows/scenarios/my_scenario.yaml``
    (falling back to ``flows/my_scenario.yaml``)

``pyyaml`` is imported lazily so importing this module (e.g. for the domain unit
tests) does not require it.
"""

import os

from domain.flow import Flow

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FLOWS_DIR = os.path.join(PROJECT_ROOT, "flows")

# Scenarios carrying any of these tags are skipped by --run-tests=all (still runnable
# by id): `template` needs real selectors; `manual` has side effects (writes data);
# `blocked` is parked (a known unmet dependency). `blocked` scenarios are also NOT tagged
# `manual`, so they stay out of `tag:manual` runs; find them via `tag:blocked`.
_SKIP_TAGS = {"template", "manual", "blocked"}


class FlowError(Exception):
    """A flow file is malformed."""


class FlowNotFound(FlowError):
    """No file exists for a requested flow id."""


def _load_yaml(path):
    import yaml  # lazy: only needed when actually reading flows
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def flow_path(flow_id, flows_dir=None):
    """Resolve a flow id to a file path (may not exist)."""
    flows_dir = flows_dir or DEFAULT_FLOWS_DIR
    if "." in flow_id:
        return os.path.join(flows_dir, *flow_id.split(".")) + ".yaml"
    for candidate in (os.path.join(flows_dir, "scenarios", flow_id + ".yaml"),
                      os.path.join(flows_dir, flow_id + ".yaml")):
        if os.path.exists(candidate):
            return candidate
    # Return the canonical scenario path so the error message is helpful.
    return os.path.join(flows_dir, "scenarios", flow_id + ".yaml")


def load_flow(flow_id, flows_dir=None):
    """Load one flow/block/scenario as a :class:`Flow` (raw, uncompiled steps)."""
    flows_dir = flows_dir or DEFAULT_FLOWS_DIR
    path = flow_path(flow_id, flows_dir)
    if not os.path.exists(path):
        raise FlowNotFound("no flow file for %r (looked at %s)" % (flow_id, path))
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise FlowError("%s must be a YAML mapping" % path)
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        raise FlowError("%s: 'steps' must be a list" % path)
    return Flow(
        id=data.get("id", flow_id),
        steps=steps,
        name=data.get("name"),
        description=data.get("description"),
        tags=data.get("tags") or [],
        source=path,
    )


def load_selectors(flows_dir=None):
    """Load the named-target -> CSS map from ``flows/selectors.yaml`` ({} if absent)."""
    flows_dir = flows_dir or DEFAULT_FLOWS_DIR
    path = os.path.join(flows_dir, "selectors.yaml")
    if not os.path.exists(path):
        return {}
    data = _load_yaml(path)
    return data if isinstance(data, dict) else {}


def discover_scenarios(flows_dir=None, include_templates=False):
    """Return the ids of runnable scenarios under ``flows/scenarios`` (sorted).

    Scenarios tagged ``template`` (need real selectors) or ``manual`` (have side
    effects, e.g. create_reclamation writes a record) are skipped by default, so
    ``--run-tests=all`` only runs safe, configured scenarios. Pass
    ``include_templates=True`` to list them too; they are always runnable by id.
    """
    flows_dir = flows_dir or DEFAULT_FLOWS_DIR
    scenarios_dir = os.path.join(flows_dir, "scenarios")
    if not os.path.isdir(scenarios_dir):
        return []
    ids = []
    for name in sorted(os.listdir(scenarios_dir)):
        if not name.endswith(".yaml"):
            continue
        if not include_templates:
            data = _load_yaml(os.path.join(scenarios_dir, name))
            if set((data or {}).get("tags") or []) & _SKIP_TAGS:
                continue
        ids.append(name[:-5])
    return ids


def scenarios_with_tag(tag, flows_dir=None):
    """Return the ids of every scenario carrying ``tag`` (sorted).

    Unlike :func:`discover_scenarios`, this does NOT skip template/manual - asking for a
    tag explicitly includes them (e.g. ``tag:manual`` returns the write scenarios).
    """
    flows_dir = flows_dir or DEFAULT_FLOWS_DIR
    scenarios_dir = os.path.join(flows_dir, "scenarios")
    if not os.path.isdir(scenarios_dir):
        return []
    ids = []
    for name in sorted(os.listdir(scenarios_dir)):
        if not name.endswith(".yaml"):
            continue
        data = _load_yaml(os.path.join(scenarios_dir, name))
        if tag in ((data or {}).get("tags") or []):
            ids.append(name[:-5])
    return ids
