"""The recorder's state machine: what it does with what the page reports.

The page is faked. What is worth pinning here is the orchestration - when a
recording starts, which steps are performed as well as written down, what happens
when the window navigates mid-recording, and above all that whatever comes out is
an ordinary scenario the compiler accepts. A recorder that produces something the
runner cannot run is worse than no recorder.
"""

import json
import os
import subprocess

import pytest

from engine import compiler, flowfile, loader, recorder

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "flows")


class FakeAdapter:
    """A page that answers whatever the test queued, and remembers what was done."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []          # every recorder_call expression, in order
        self.performed = []      # (action, selector, value)
        self.setups = []
        self.rendered = []
        self.disconnected = False

    # -- what the recorder uses ----------------------------------------------
    def recorder_setup(self, js_source):
        self.setups.append(js_source)

    def recorder_call(self, expression, argument=None):
        self.calls.append(expression)
        if expression.startswith("recorder.render"):
            self.rendered.append(argument)
            return None
        if expression.startswith("{running:"):
            return self.answers.pop(0) if self.answers else None
        return None

    def click(self, selector, timeout=None):
        self.performed.append(("click", selector, None))

    def fill(self, selector, value, timeout=None):
        self.performed.append(("fill", selector, value))

    def select(self, selector, value, timeout=None):
        self.performed.append(("select", selector, value))

    def press_key(self, key):
        self.performed.append(("press", None, key))

    def goto(self, url, timeout=None):
        self.performed.append(("goto", url, None))

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A writable flows tree in front of the fixtures, as an installed build has."""
    user = tmp_path / "user-flows"
    (user / "scenarios").mkdir(parents=True)
    monkeypatch.setattr(loader.runtime_paths, "flows_search_path",
                        lambda: [str(user), FIXTURES])
    return user


def _answer(running=True, request=False, events=()):
    return {"running": running, "request": request, "events": list(events)}


def _step_event(action, target, selector=None, value=None, named=False):
    return {"kind": "step", "step": {"action": action, "target": target,
                                     "selector": selector or target,
                                     "value": value, "named": named}}


def _recording(tree, scenario_id="recorded"):
    return recorder.Recording("session-1", scenario_id, None)


# ------------------------------------------------------------------- starting

def test_nothing_happens_until_the_menu_item_is_used(tree):
    """The whole promise: ordinary browsing never becomes a step."""
    adapter = FakeAdapter([_answer(running=False, request=False)])
    rec = _recording(tree)
    recorder._tick(adapter, rec, {}, {})
    assert not rec.active
    assert rec.steps == []
    assert not any(c.startswith("recorder.start") for c in adapter.calls)


def test_the_request_from_the_extension_starts_a_recording(tree):
    adapter = FakeAdapter([_answer(running=False, request=True)])
    rec = _recording(tree)
    recorder._tick(adapter, rec, {"dashboard": ".navbar"}, {})
    assert rec.active
    assert any(c.startswith("recorder.start") for c in adapter.calls)
    # The page is told the vocabulary and the selectors, so its menu and its
    # naming cannot drift from what the compiler and the tree actually have.
    assert any(c.startswith("recorder.configure") for c in adapter.calls)


def test_a_page_with_no_recorder_is_not_an_error(tree):
    # Mid-navigation the evaluate returns nothing at all; that is a normal tick.
    adapter = FakeAdapter([None])
    rec = _recording(tree)
    recorder._tick(adapter, rec, {}, {})
    assert not rec.active


# ------------------------------------------------------------------ capturing

def test_a_captured_step_is_performed_and_recorded(tree):
    rec = _recording(tree)
    rec.active = True
    adapter = FakeAdapter([_answer(events=[_step_event("click", "menu_settings",
                                                       selector=".o_menu")])])
    recorder._tick(adapter, rec, {}, {})
    # Performed against the element that was picked, not the name: a name from
    # selectors.yaml may match several things and the user pointed at one.
    assert adapter.performed == [("click", ".o_menu", None)]
    # Recorded under the name, so the scenario reads like the tree it joins.
    assert rec.steps == [{"action": "click", "target": "menu_settings", "value": None}]


def test_an_assertion_is_recorded_without_being_performed(tree):
    rec = _recording(tree)
    rec.active = True
    adapter = FakeAdapter([_answer(events=[_step_event("assert_visible", "dashboard")])])
    recorder._tick(adapter, rec, {}, {})
    assert adapter.performed == []          # nothing to do; it changes nothing
    assert rec.steps[0]["action"] == "assert_visible"


def test_a_value_step_carries_its_value(tree):
    rec = _recording(tree)
    rec.active = True
    adapter = FakeAdapter([_answer(events=[_step_event("fill", "login_input",
                                                       value="admin")])])
    recorder._tick(adapter, rec, {}, {})
    assert adapter.performed == [("fill", "login_input", "admin")]
    assert rec.steps[0]["value"] == "admin"


def test_a_step_that_will_not_take_effect_is_still_recorded(tree):
    """What the user asked for is the step; that it failed now is a warning."""
    rec = _recording(tree)
    rec.active = True
    adapter = FakeAdapter([_answer(events=[_step_event("click", "gone")])])

    def boom(selector, timeout=None):
        raise RuntimeError("element detached")

    adapter.click = boom
    recorder._tick(adapter, rec, {}, {})
    assert rec.steps[0]["action"] == "click"


def test_the_panel_is_repainted_after_every_capture(tree):
    rec = _recording(tree)
    rec.active = True
    adapter = FakeAdapter([_answer(events=[_step_event("click", "a"),
                                           _step_event("click", "b")])])
    recorder._tick(adapter, rec, {}, {})
    assert len(adapter.rendered) == 2
    assert [s["target"] for s in adapter.rendered[-1]["steps"]] == ["a", "b"]


# ---------------------------------------------------------------- navigation

def test_a_navigation_brings_the_panel_back_with_its_steps(tree):
    """The renderer survives a new document; its state does not, so Python re-seeds it."""
    rec = _recording(tree)
    rec.active = True
    rec.add({"action": "click", "target": "first", "value": None})
    adapter = FakeAdapter([_answer(running=False)])
    recorder._tick(adapter, rec, {}, {})
    assert any(c.startswith("recorder.start") for c in adapter.calls)
    assert adapter.rendered[-1]["steps"] == [{"action": "click", "target": "first",
                                              "value": ""}]
    assert rec.active                      # still the same recording


# ------------------------------------------------------------------ finishing

def test_finishing_writes_a_scenario_that_compiles(tree):
    """The property that matters most: a recording is an ordinary scenario."""
    rec = _recording(tree, "recorded_smoke")
    rec.active = True
    adapter = FakeAdapter([_answer(events=[
        _step_event("click", "menu_settings"),
        _step_event("fill", "login_input", value="admin"),
        _step_event("assert_visible", "dashboard"),
        {"kind": "finish"},
    ])])
    recorder._tick(adapter, rec, {}, {})

    assert rec.finished and not rec.active
    path = os.path.join(str(tree), "scenarios", "recorded_smoke.yaml")
    assert os.path.exists(path)
    steps = compiler.compile_scenario("recorded_smoke", selectors=loader.load_selectors(),
                                      ctx=flowfile.CHECK_CONTEXT)
    assert [s.action for s in steps] == ["click", "fill", "assert_visible"]
    assert any(c.startswith("recorder.stop") for c in adapter.calls)


def test_a_recording_is_tagged_template(tree):
    # A draft should not join --run-tests=all until someone has looked at it.
    rec = _recording(tree, "draft")
    rec.add({"action": "assert_visible", "target": "dashboard", "value": None})
    assert rec.save()["ok"]
    assert loader.load_flow("draft").tags == ["template"]


def test_the_default_name_is_a_timestamp(tree):
    rec = recorder.Recording("s", None, None)
    assert rec.scenario_id.startswith("recorded_")
    assert flowfile.safe_id(rec.scenario_id) == rec.scenario_id


def test_an_empty_recording_still_produces_a_valid_file(tree):
    rec = _recording(tree, "nothing")
    result = rec.save()
    assert result["ok"]
    assert loader.load_flow("nothing").steps == []


# -------------------------------------------------------------- what we ship

def test_the_in_page_recorder_is_valid_javascript():
    """It is shipped as source and evaluated in the page; a typo is a dead panel."""
    node = _node()
    if not node:
        pytest.skip("no node available to parse the recorder")
    for name in ("recorder.js", "hud.js"):
        path = os.path.join(os.path.dirname(os.path.abspath(recorder.__file__)), name)
        done = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert done.returncode == 0, "%s: %s" % (name, done.stderr)


def test_the_extension_is_valid_javascript_and_json():
    node = _node()
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "extensions", "_recorder")
    with open(os.path.join(root, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["manifest_version"] == 3
    assert "contextMenus" in manifest["permissions"]
    if not node:
        pytest.skip("no node available to parse the extension")
    for name in ("background.js", "content.js"):
        done = subprocess.run([node, "--check", os.path.join(root, name)],
                              capture_output=True, text=True)
        assert done.returncode == 0, "%s: %s" % (name, done.stderr)


def _node():
    import shutil

    return shutil.which("node")


# ------------------------------------------------- the two that bit in testing
# Both were invisible from Python: one threw inside a swallowed evaluate, the
# other made the recorder eat its own menu. They are the reason these exist.

def test_the_evaluate_wrapper_binds_the_name_the_callers_use():
    """recorder_call's little expressions are written against `recorder`.

    Binding it to anything else makes every poll throw a ReferenceError, which
    the adapter swallows by design - so the recorder simply never starts and
    nothing anywhere says why.
    """
    from adapters.playwright_adapter import PlaywrightAdapter

    class FakePage:
        def __init__(self):
            self.expression = None

        def evaluate(self, expression, argument=None):
            self.expression = expression
            return {"ok": True}

    adapter = PlaywrightAdapter.__new__(PlaywrightAdapter)
    adapter._page = FakePage()
    assert adapter.recorder_call("recorder.running()") == {"ok": True}
    assert "const recorder = window.__Recorder" in adapter._page.expression


def test_a_click_on_the_recorders_own_panel_is_left_alone():
    """Arming must not swallow the menu it just opened.

    While armed the recorder takes every click, so it has to recognise its own.
    Events leaving a shadow tree are retargeted to the host, so a click on a menu
    item arrives as the host element - which is exactly what has to pass through.
    """
    node = _node()
    if not node:
        pytest.skip("no node available to run the recorder")
    path = os.path.join(os.path.dirname(os.path.abspath(recorder.__file__)),
                        "recorder.js")
    harness = """
      const HOST = '__cms_recorder_host__';
      const host = {id: HOST, contains: (el) => el && el.inside === true};
      global.document = {
        getElementById: (id) => (id === HOST ? host : null),
        createElement: () => ({style: {}, setAttribute() {}, appendChild() {}}),
        documentElement: {appendChild() {}, hasAttribute: () => false,
                          removeAttribute() {}},
        addEventListener() {}, removeEventListener() {}
      };
      global.window = {};
      require(%s);
      const r = window.__Recorder;
      const results = {
        host: r._ours({target: host}),
        inside: r._ours({target: {inside: true}}),
        page: r._ours({target: {inside: false, id: 'app'}}),
        nothing: r._ours({target: null})
      };
      console.log(JSON.stringify(results));
    """ % json.dumps(path)
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    results = json.loads(done.stdout.strip())
    assert results["host"] is True         # the retargeted case: a menu click
    assert results["inside"] is True
    assert results["nothing"] is True      # nothing to pick is not a pick
    assert results["page"] is False        # an ordinary element IS pickable
