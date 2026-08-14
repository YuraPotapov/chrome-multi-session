"""Compile a scenario into a flat, executable list of Steps.

Responsibilities (the YAML format is documented in docs/flows.md):
  - resolve ``use:`` references and expand the referenced block's steps inline,
    recursively (this is what makes ``auth.login`` reusable);
  - detect cyclic ``use:`` chains;
  - normalize both YAML step shapes (verbose ``type:`` and shorthand single-key)
    into :class:`domain.flow.Step`, validating required fields;
  - resolve named targets to CSS via the selectors map and substitute
    ``{{params}}``.

The engine never executes YAML directly - only the compiled plan.
"""

import logging

from domain.flow import Step, USE
from domain.plan import PlanNode, GROUP, STEP
from engine import loader
from engine.context import substitute

log = logging.getLogger("flowengine.compiler")


class CompileError(Exception):
    """A scenario/block could not be compiled (bad step, cycle, missing flow)."""


# Actions grouped by the shape of their argument.
SELECTOR_ONLY = {"click", "wait_for", "assert_exists", "assert_visible", "assert_not_visible"}
SELECTOR_AND_VALUE = {"fill", "select", "assert_text_contains"}
# Scalar-argument steps: the shorthand arg becomes ``value`` (e.g. `press: Enter`).
VALUE_ONLY = {"assert_url_contains", "assert_title", "assert_host_up", "press"}
URL_TARGET = {"goto"}
# Actions whose ``target`` is a selector (so it goes through the selectors map).
SELECTOR_TARGET = SELECTOR_ONLY | SELECTOR_AND_VALUE
KNOWN = SELECTOR_ONLY | SELECTOR_AND_VALUE | VALUE_ONLY | URL_TARGET | {USE}


def parse_step(raw, source=None):
    """Normalize one raw YAML step mapping into a :class:`Step`."""
    if not isinstance(raw, dict):
        raise CompileError("step must be a mapping, got %r" % (raw,))

    if "type" in raw:  # verbose form: supports timeout/retry/state
        step = Step(action=raw["type"], target=raw.get("target"), value=raw.get("value"),
                    state=raw.get("state"), timeout=raw.get("timeout"),
                    retry=raw.get("retry"), source=source)
    else:  # shorthand form: exactly one action key
        keys = list(raw.keys())
        if len(keys) != 1:
            raise CompileError("shorthand step needs exactly one action key: %r" % (raw,))
        action, arg = keys[0], raw[keys[0]]
        step = Step(action=action, source=source)
        if action == USE or action in URL_TARGET or action in SELECTOR_ONLY:
            step.target = arg
        elif action in VALUE_ONLY:
            step.value = arg
        elif action in SELECTOR_AND_VALUE:
            if not isinstance(arg, dict):
                raise CompileError("%s needs a {target, value} mapping: %r" % (action, raw))
            step.target = arg.get("target")
            step.value = arg.get("value")
            step.timeout = arg.get("timeout")
        # unknown action falls through to _validate for a clear error

    _validate(step, raw)
    return step


def _validate(step, raw):
    action = step.action
    if action not in KNOWN:
        raise CompileError("unknown action %r in step %r" % (action, raw))
    if action == USE:
        if not step.target:
            raise CompileError("use step needs a flow id: %r" % (raw,))
    elif action in URL_TARGET and not step.target:
        raise CompileError("goto needs a url: %r" % (raw,))
    elif action in SELECTOR_ONLY and not step.target:
        raise CompileError("%s needs a target selector: %r" % (action, raw))
    elif action in SELECTOR_AND_VALUE and (step.target is None or step.value is None):
        raise CompileError("%s needs target and value: %r" % (action, raw))
    elif action in VALUE_ONLY and step.value is None:
        raise CompileError("%s needs a value: %r" % (action, raw))


def _finalize(step, selectors, ctx):
    """Resolve a named selector to CSS and substitute params (in place)."""
    if step.action in SELECTOR_TARGET and step.target in (selectors or {}):
        step.target = selectors[step.target]
    if step.target is not None:
        step.target = substitute(step.target, ctx)
    if step.value is not None:
        step.value = substitute(step.value, ctx)
    return step


def _leaf_label(action, target, value):
    """A short human label for a leaf step (``click apps_menu``, ``fill x = "y"``).

    Built from the *named* target (before it is resolved to CSS) and the resolved
    value, so the tree reads friendlier than the raw selectors.
    """
    if target and value is not None:
        return '%s %s = "%s"' % (action, target, value)
    if target:
        return "%s %s" % (action, target)
    if value is not None:
        return '%s "%s"' % (action, value)
    return action


def compile_plan(scenario_id, flows_dir=None, selectors=None, ctx=None):
    """Return ``(steps, root)`` for ``scenario_id``.

    ``steps`` is the flat, executable ``list[Step]`` (identical to what
    :func:`compile_scenario` returns); ``root`` is a :class:`PlanNode` tree that
    mirrors the ``use:``-block nesting down to per-step leaves. Leaves are added
    to ``steps`` in the same DFS order the tree is built, so each leaf's
    ``step_index`` matches the runner's ``enumerate(steps)`` position 1:1.
    """
    # No default of our own: None means "wherever the loader looks", which since
    # there can be more than one tree is a search path, not a directory.
    selectors = selectors or {}
    stack = []  # flow ids currently being expanded, for cycle detection
    steps = []

    def expand(flow_id, node_id):
        if flow_id in stack:
            raise CompileError("cyclic use: %s" % " -> ".join(stack + [flow_id]))
        flow = loader.load_flow(flow_id, flows_dir)
        stack.append(flow_id)
        group = PlanNode(id=node_id, label=flow.name or flow.id, kind=GROUP)
        for i, raw in enumerate(flow.steps):
            child_id = "%s/%d" % (node_id, i)
            step = parse_step(raw, source=flow_id)
            if step.action == USE:
                group.children.append(expand(step.target, child_id))
            else:
                named_target = step.target        # friendly name, before CSS resolution
                _finalize(step, selectors, ctx)
                index = len(steps)
                steps.append(step)
                group.children.append(PlanNode(
                    id=child_id, kind=STEP, step_index=index, action=step.action,
                    target=named_target, label=_leaf_label(step.action, named_target, step.value)))
        stack.pop()
        return group

    root = expand(scenario_id, "0")
    return steps, root


def compile_scenario(scenario_id, flows_dir=None, selectors=None, ctx=None):
    """Return the flat list[Step] for ``scenario_id`` with blocks expanded."""
    steps, _root = compile_plan(scenario_id, flows_dir, selectors, ctx)
    return steps
