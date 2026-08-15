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

def test_the_panel_appears_without_being_asked_for(tree):
    """A window launched with --recorder is a window being recorded."""
    adapter = FakeAdapter([_answer(running=False)])
    rec = _recording(tree)
    recorder._tick(adapter, rec, {"dashboard": ".navbar"}, {})
    assert rec.active
    assert any(c.startswith("recorder.start") for c in adapter.calls)
    # The page is told the vocabulary and the selectors, so its menu and its
    # naming cannot drift from what the compiler and the tree actually have.
    assert any(c.startswith("recorder.configure") for c in adapter.calls)


def test_showing_the_panel_is_not_capturing(tree):
    """The whole promise: ordinary browsing never becomes a step."""
    adapter = FakeAdapter([_answer(running=False), _answer(events=[])])
    rec = _recording(tree)
    recorder._tick(adapter, rec, {}, {})
    recorder._tick(adapter, rec, {}, {})
    assert rec.steps == []


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
    rec.active = True   # already announced; a navigation must not announce again
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


def test_the_recorder_needs_no_extension_of_its_own():
    """It is shown by the launcher, not summoned from a menu.

    The extension existed only to carry a right-click into the page. Showing the
    panel on attach removes the extension, the contextMenus permission, the
    per-profile install and the DOM flag they talked over.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "extensions", "_recorder"))
    with open(os.path.join(root, "engine", "recorder.js"), encoding="utf-8") as handle:
        source = handle.read()
    assert "takeRequest" not in source
    assert "data-cms-record" not in source


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


def test_a_value_is_never_asked_for_with_window_prompt():
    """`fill` asked for its value with window.prompt, and so never worked.

    The recorder is driven over CDP, and Playwright dismisses a page's dialogs by
    default: prompt() returned null, the step was dropped, and nothing anywhere
    said why. The end-to-end test did not catch it because the driver stubbed
    window.prompt - it tested around the bug. Asking has to happen inside the
    recorder's own panel, where no dialog is involved.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(recorder.__file__)),
                        "recorder.js")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    # Read the code, not the comments: the comment explaining why this rule
    # exists names the very functions it forbids.
    code = "\n".join(line.split("//")[0] for line in source.splitlines())
    for banned in ("prompt(", "confirm(", "alert("):
        assert banned not in code, "recorder.js still calls %s" % banned
    # What replaced it: an input rendered into the shadow DOM.
    assert "_askValue" in source and "menu-value" in source


# --------------------------------------------------- continuing what exists
# A recording is usually one more pass over something half-written. Naming an
# existing scenario has to add to it, not replace it - the steps already there
# are work, and so are its name and its tags.

def test_naming_an_existing_scenario_loads_its_steps(tree):
    flowfile.save("wip", meta={"name": "Half done", "tags": ["smoke", "wip"]},
                  steps=[{"action": "use", "target": "auth.login"},
                         {"action": "click", "target": "menu_settings"}])
    rec = recorder.Recording("s", "wip", None)
    assert rec.continuing
    assert [s["action"] for s in rec.steps] == ["use", "click"]


def test_continuing_appends_and_keeps_what_the_file_said(tree):
    flowfile.save("wip", meta={"name": "Half done", "description": "mine",
                               "tags": ["smoke", "wip"]},
                  steps=[{"action": "click", "target": "menu_settings"}])
    rec = recorder.Recording("s", "wip", None)
    rec.add({"action": "assert_visible", "target": "dashboard", "value": None})
    assert rec.save()["ok"]

    flow = loader.load_flow("wip")
    assert [compiler.parse_step(s).action for s in flow.steps] == [
        "click", "assert_visible"]
    # Its own name and tags are edits somebody made; a recorder does not undo them.
    assert flow.name == "Half done"
    assert flow.tags == ["smoke", "wip"]
    assert flow.description == "mine"


def test_a_new_recording_is_not_marked_as_continuing(tree):
    rec = recorder.Recording("s", "brand_new", None)
    assert not rec.continuing and rec.steps == []
    assert rec.scenario_id == "brand_new"


def test_recording_into_a_bundled_scenario_is_refused(tree):
    """Otherwise the steps are collected and then thrown away at the end."""
    with pytest.raises(recorder.FlowNotWritable) as exc:
        recorder.Recording("s", "demo_smoke", None)
    assert "duplicate it" in str(exc.value)


def test_continuing_survives_a_navigation_with_every_step(tree):
    # The panel is re-seeded from Python, so what it shows after a reload has to
    # include the steps that were already in the file.
    flowfile.save("wip", steps=[{"action": "click", "target": "first"}])
    rec = recorder.Recording("s", "wip", None)
    rec.active = True
    adapter = FakeAdapter([_answer(running=False)])
    recorder._tick(adapter, rec, {}, {})
    assert [s["target"] for s in adapter.rendered[-1]["steps"]] == ["first"]


def test_a_selector_is_never_just_a_tag_name():
    """`click: "a"` was a real recording, and it means every link on the page.

    Synthesis used to give up after four ancestors and return whatever it had.
    For a plain <a> in an unremarkable list - Odoo's search dropdown, where this
    came from - that is the tag on its own. Counting position among siblings is
    ugly and it is still infinitely better than that.
    """
    node = _node()
    if not node:
        pytest.skip("no node available to run the recorder")
    path = os.path.join(os.path.dirname(os.path.abspath(recorder.__file__)),
                        "recorder.js")
    # A dropdown of bare anchors, which is the shape that produced "a".
    harness = """
      const HOST = '__cms_recorder_host__';
      // Minimal DOM: enough for querySelectorAll/matches over one small tree.
      const anchors = [];
      const list = {tagName: 'UL', className: 'o_searchview_autocomplete',
                    getAttribute: () => null, id: '', children: anchors,
                    parentElement: null};
      for (let i = 0; i < 3; i++) {
        anchors.push({tagName: 'A', className: '', id: '', children: [],
                      getAttribute: () => null, parentElement: list});
      }
      function matchesSel(el, sel) {
        if (sel === 'a') return el.tagName === 'A';
        if (sel === 'ul.o_searchview_autocomplete') return el === list;
        const m = sel.match(/^a:nth-of-type\\((\\d+)\\)$/);
        if (m) return el.tagName === 'A' && anchors.indexOf(el) === +m[1] - 1;
        const s = sel.match(/^ul\\.o_searchview_autocomplete a:nth-of-type\\((\\d+)\\)$/);
        if (s) return el.tagName === 'A' && anchors.indexOf(el) === +s[1] - 1;
        if (sel === 'ul.o_searchview_autocomplete a') return el.tagName === 'A';
        return false;
      }
      const all = [list].concat(anchors);
      global.document = {
        getElementById: () => null,
        querySelectorAll: (sel) => all.filter(e => matchesSel(e, sel)),
        createElement: () => ({style: {}, setAttribute() {}, appendChild() {}}),
        documentElement: {appendChild() {}, hasAttribute: () => false,
                          removeAttribute() {}},
        addEventListener() {}, removeEventListener() {}
      };
      anchors.forEach(a => { a.matches = (s) => matchesSel(a, s); });
      list.matches = (s) => matchesSel(list, s);
      global.window = {};
      require(%s);
      const r = window.__Recorder;
      r.configure({}, {});
      console.log(JSON.stringify(r._describe(anchors[1])));
    """ % json.dumps(path)
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    described = json.loads(done.stdout.strip())
    assert described["selector"] != "a"
    assert "nth-of-type(2)" in described["selector"]
    assert described["unique"] is True


def test_a_radio_offers_a_way_to_check_which_one_is_on():
    """"Is this option selected?" is the question none of the assertions answer.

    CSS says it exactly - :checked - so it needs no new step type, and the tree
    already writes selectors that way by hand
    (flows/selectors.yaml: roles_wizard_agent_checked). What the recorder has to
    do is find the input behind the label somebody actually clicked, and name
    which option it is: every radio in a group shares its `name`.
    """
    node = _node()
    if not node:
        pytest.skip("no node available to run the recorder")
    path = os.path.join(os.path.dirname(os.path.abspath(recorder.__file__)),
                        "recorder.js")
    # Odoo's shape: a wrapping div per option, the input carrying data-value.
    harness = """
      const options = ['agent', 'manager', 'ov_manager'];
      const inputs = [], wrappers = [];
      options.forEach((value, i) => {
        const input = {tagName: 'INPUT', className: 'form-check-input o_radio_input',
                       id: '', children: [], querySelectorAll: () => [],
                       attrs: {type: 'radio', name: 'role', 'data-value': value}};
        input.getAttribute = (k) => input.attrs[k] || null;
        const wrap = {tagName: 'DIV', className: 'form-check o_radio_item', id: '',
                      children: [input], getAttribute: () => null,
                      querySelectorAll: (s) => (/radio|checkbox/.test(s) ? [input] : [])};
        input.parentElement = wrap;
        inputs.push(input); wrappers.push(wrap);
      });
      const field = {tagName: 'DIV', className: 'o_field_widget', id: '',
                     children: wrappers, parentElement: null,
                     getAttribute: (k) => (k === 'name' ? 'role' : null),
                     querySelectorAll: () => inputs};
      wrappers.forEach(w => { w.parentElement = field; });
      const all = [field].concat(wrappers, inputs);
      function sel(el, s) {
        if (s === '[name="role"]') return el === field || inputs.includes(el);
        if (s === 'div.form-check.o_radio_item') return wrappers.includes(el);
        const m = s.match(/^input\\[type="radio"\\]\\[name="role"\\]\\[data-value="([a-z_]+)"\\]$/);
        if (m) return inputs.includes(el) && el.getAttribute('data-value') === m[1];
        return false;
      }
      global.document = {
        getElementById: () => null,
        querySelectorAll: (s) => all.filter(e => sel(e, s)),
        createElement: () => ({style: {}, setAttribute() {}, appendChild() {}}),
        documentElement: {appendChild() {}, hasAttribute: () => false,
                          removeAttribute() {}},
        addEventListener() {}, removeEventListener() {}
      };
      all.forEach(e => { e.matches = (s) => sel(e, s); });
      global.window = {};
      require(%s);
      const r = window.__Recorder;
      r.configure({}, {selector_only: ['click', 'wait_for', 'assert_exists',
                                       'assert_visible', 'assert_not_visible'],
                       selector_and_value: ['fill'], value_only: ['press'],
                       url_target: ['goto']});
      // Picking the wrapper, which is what clicking the label lands on.
      const described = r._describe(wrappers[2]);
      console.log(JSON.stringify({checkable: described.checkable,
                                  actions: r._actionsFor(described)}));
    """ % json.dumps(path)
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip())

    # It found the input behind the label, and named which option it is.
    assert out["checkable"] == 'input[type="radio"][name="role"][data-value="ov_manager"]'
    offered = {(a["action"], a["selector"]) for a in out["actions"]}
    assert ("assert_exists", out["checkable"] + ":checked") in offered
    assert ("assert_exists", out["checkable"] + ":not(:checked)") in offered
    assert ("wait_for", out["checkable"] + ":checked") in offered


# ----------------------------------------------------- fixing it as you go
# A bad capture is obvious while the page is still on screen and much less so
# afterwards, so the panel can delete, reorder and retarget. Python owns the
# list; the panel sends an intent and repaints from what comes back.

def _amend(rec, kind, **fields):
    adapter = FakeAdapter([_answer(events=[dict(fields, kind=kind)])])
    recorder._tick(adapter, rec, {}, {})
    return adapter


def _three(tree):
    rec = _recording(tree)
    rec.active = True
    for target in ("first", "second", "third"):
        rec.add({"action": "click", "target": target, "value": None})
    return rec


def test_a_step_can_be_deleted(tree):
    rec = _three(tree)
    _amend(rec, "delete", index=1)
    assert [s["target"] for s in rec.steps] == ["first", "third"]


def test_a_step_can_be_moved(tree):
    rec = _three(tree)
    _amend(rec, "move", index=0, delta=1)
    assert [s["target"] for s in rec.steps] == ["second", "first", "third"]
    _amend(rec, "move", index=2, delta=-1)
    assert [s["target"] for s in rec.steps] == ["second", "third", "first"]


def test_moving_off_either_end_does_nothing(tree):
    rec = _three(tree)
    _amend(rec, "move", index=0, delta=-1)
    _amend(rec, "move", index=2, delta=1)
    assert [s["target"] for s in rec.steps] == ["first", "second", "third"]


def test_a_step_can_be_retargeted(tree):
    rec = _three(tree)
    _amend(rec, "edit", index=1, target="menu_settings", value="typed")
    assert rec.steps[1]["target"] == "menu_settings"
    assert rec.steps[1]["value"] == "typed"


def test_clearing_a_value_means_no_value(tree):
    # Not the same as leaving it alone: an emptied box removes it from the step.
    rec = _three(tree)
    rec.steps[1]["value"] = "was here"
    _amend(rec, "edit", index=1, target="second", value="")
    assert rec.steps[1]["value"] is None


def test_an_empty_target_leaves_the_step_pointing_where_it_did(tree):
    rec = _three(tree)
    _amend(rec, "edit", index=1, target="", value=None)
    assert rec.steps[1]["target"] == "second"


def test_a_stale_index_is_ignored(tree):
    """The panel can be a repaint behind; a click on a row that moved must not
    delete whatever is there now."""
    rec = _three(tree)
    _amend(rec, "delete", index=9)
    _amend(rec, "delete", index=-1)
    assert len(rec.steps) == 3


def test_the_panel_is_repainted_after_an_amendment(tree):
    rec = _three(tree)
    adapter = _amend(rec, "delete", index=0)
    assert [s["target"] for s in adapter.rendered[-1]["steps"]] == ["second", "third"]


def test_what_is_saved_is_what_the_panel_last_showed(tree):
    rec = _three(tree)
    _amend(rec, "delete", index=1)
    _amend(rec, "edit", index=0, target="menu_settings", value=None)
    assert rec.save()["ok"]
    flow = loader.load_flow(rec.scenario_id)
    assert [compiler.parse_step(s).target for s in flow.steps] == [
        "menu_settings", "third"]
