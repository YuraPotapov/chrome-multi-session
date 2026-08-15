"""Drive the Scenario Recorder: attach, wait to be asked, capture, write.

The recorder is the flow engine run backwards. Instead of reading a scenario and
performing it, it watches someone perform one and writes it down - and it writes
it in the same grammar, through the same validator, into the same tree, so what
comes out is an ordinary scenario with nothing special about it.

How a recording starts is the part worth explaining. The user is already working
in one of the launched windows; they right-click and choose "Start Scenarios".
That menu item belongs to a small bundled extension, which cannot reach the page's
own globals - a content script lives in an isolated world - so it leaves a mark on
<html> instead. This module is attached over CDP from the moment the window opens
and polls for that mark. There is no server, no port and no localhost permission
anywhere in it: the DOM is the only channel needed, because both sides are already
in the same document.

Polling is not a compromise here either. Playwright's sync API is greenlet-based,
so a callback can only run while this thread is inside a Playwright call - an idle
recorder has to pump regardless. Given that, asking the page for its queue costs
nothing extra and survives a navigation for free: a fresh document simply answers
with an empty one.
"""

import logging
import os
import threading
import time

from engine import events, flowfile, loader, runner

log = logging.getLogger("session_launcher.recorder")

_RECORDER_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "recorder.js")

#: How often to ask the page whether anything happened. Fast enough that picking
#: an action feels immediate, slow enough to be invisible on the machine.
POLL_SECONDS = 0.15

#: Actions the recorder performs as well as records, so the page ends up in the
#: state the replayed flow will reach. Assertions are deliberately absent: they
#: change nothing, and "performing" one would only mean waiting for what is
#: already on screen.
PERFORMED = ("click", "fill", "select", "press", "goto")


def _load_recorder_js():
    with open(_RECORDER_JS_PATH, encoding="utf-8") as handle:
        return handle.read()


RECORDER_JS = _load_recorder_js()


class Recording:
    """One window being recorded: the steps so far, and what to do with them."""

    def __init__(self, session_name, scenario_id=None, flows_dir=None):
        self.session = session_name
        self.scenario_id = scenario_id or _default_scenario_id()
        self.flows_dir = flows_dir
        self.steps = []
        self.active = False
        self.finished = False

    def state(self):
        """What the in-page panel paints itself from."""
        return {"scenario": self.scenario_id, "status": "recording",
                "steps": [{"action": s["action"],
                           "target": s.get("target") or "",
                           "value": s.get("value") or ""} for s in self.steps]}

    def add(self, step):
        self.steps.append(step)

    def save(self):
        """Write what was captured as a scenario. Returns the core's result dict.

        Tagged ``template`` because a recording is a draft: it should not join
        ``--run-tests=all`` until someone has looked at it and taken the tag off.
        """
        return flowfile.save(
            self.scenario_id, self.flows_dir,
            meta={"id": self.scenario_id,
                  "name": "Recorded %s" % self.scenario_id,
                  "description": "Captured with the Scenario Recorder.",
                  "tags": list(flowfile.DEFAULT_TAGS)},
            steps=[{key: value for key, value in step.items()
                    if key in ("action", "target", "value", "timeout", "state")}
                   for step in self.steps])


def _default_scenario_id():
    return flowfile.safe_id("recorded_%s" % time.strftime("%Y%m%d_%H%M%S"))


def record_sessions(sessions, env=None, flows_dir=None, scenario_id=None,
                    stop_event=None):
    """Attach a recorder to every launched window and wait to be asked.

    One thread per window, because Playwright's sync API is bound to the thread
    that created it - the same rule ``engine.runner`` follows for parallel runs.
    Returns when ``stop_event`` is set, which is what CTRL+C and the GUI's Stop
    both come down to.
    """
    stop_event = stop_event or threading.Event()
    threads = []
    for index, session in enumerate(sessions):
        cls, _proc, profile, login, origin = session[:5]
        name = os.path.basename(profile) or login or ("session-%d" % index)
        thread = threading.Thread(
            target=_record_one, name="recorder-%d" % index, daemon=True,
            args=(name, profile, origin, flows_dir,
                  # Only the first window gets the requested id; the others would
                  # otherwise all write to the same file.
                  scenario_id if index == 0 else None, stop_event))
        thread.start()
        threads.append(thread)
    if not threads:
        log.error("No windows to record.")
        return 1
    log.info("Recorder ready. Right-click in a window and choose \"Start Scenarios\".")
    events.emit("recorder.ready", sessions=len(threads))
    try:
        while not stop_event.is_set() and any(t.is_alive() for t in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop_event.set()
    for thread in threads:
        thread.join(timeout=5)
    return 0


def _record_one(session_name, profile, origin, flows_dir, scenario_id, stop_event):
    """Attach to one window and pump it until asked to stop."""
    try:
        endpoint = runner.wait_for_devtools(profile)
        # Lazy, as everywhere else: playwright is only needed once a window is
        # actually being driven.
        from adapters.playwright_adapter import PlaywrightAdapter
        adapter = PlaywrightAdapter.connect(endpoint, runner.DEFAULT_TIMEOUT_MS)
    except Exception as exc:
        log.error("[%s] could not attach: %s", session_name, exc)
        events.emit("recorder.attach_failed", session=session_name, error=str(exc))
        return
    events.emit("recorder.attached", session=session_name, endpoint=endpoint)

    selectors = loader.load_selectors(flows_dir)
    grammar = _grammar()
    recording = Recording(session_name, scenario_id, flows_dir)
    adapter.recorder_setup(RECORDER_JS)
    _configure(adapter, selectors, grammar)

    try:
        while not stop_event.is_set():
            _tick(adapter, recording, selectors, grammar)
            time.sleep(POLL_SECONDS)
    finally:
        if recording.active and recording.steps and not recording.finished:
            # The window was closed mid-recording. Keep the work rather than
            # discarding it because nobody pressed Finish.
            _finish(adapter, recording)
        try:
            adapter.disconnect()
        except Exception:
            pass


def _tick(adapter, recording, selectors, grammar):
    """One poll: has anything happened, and does the page still have a recorder?"""
    answer = adapter.recorder_call(
        "{running: recorder.running(), request: recorder.takeRequest(),"
        " events: recorder.drain()}")
    if not answer:
        return                      # navigating, or the page has no recorder yet

    if not recording.active:
        if answer.get("request"):
            _begin(adapter, recording, selectors, grammar)
        return

    # The page kept the renderer across a navigation but not its state, so a
    # recorder that is not running means "new document": set it up again, and the
    # panel comes back with every step still in it.
    if not answer.get("running"):
        _configure(adapter, selectors, grammar)
        adapter.recorder_call("recorder.start()")
        adapter.recorder_call("recorder.render(arg)", recording.state())
        return

    for event in answer.get("events") or []:
        _handle(adapter, recording, event)


def _begin(adapter, recording, selectors, grammar):
    recording.active = True
    _configure(adapter, selectors, grammar)
    adapter.recorder_call("recorder.start()")
    adapter.recorder_call("recorder.render(arg)", recording.state())
    log.info("[%s] recording into %s", recording.session, recording.scenario_id)
    events.emit("recorder.started", session=recording.session,
                scenario=recording.scenario_id)


def _configure(adapter, selectors, grammar):
    adapter.recorder_call("recorder.configure(arg.selectors, arg.grammar)",
                          {"selectors": selectors, "grammar": grammar})


def _handle(adapter, recording, event):
    kind = (event or {}).get("kind")
    if kind == "step":
        _capture(adapter, recording, event.get("step") or {})
    elif kind == "finish":
        _finish(adapter, recording)


def _capture(adapter, recording, raw):
    """Perform what was picked, then write it down."""
    action = raw.get("action")
    if not action:
        return
    step = {"action": action, "target": raw.get("target") or None,
            "value": raw.get("value")}
    if action in PERFORMED:
        # Perform against the exact element that was picked, not the named
        # selector: a name from selectors.yaml may match several things, and the
        # user pointed at one of them.
        selector = raw.get("selector") or step["target"]
        try:
            _perform(adapter, action, selector, step.get("value"))
        except Exception as exc:
            # Record it anyway. The step is what the user asked for; that it did
            # not take effect right now is worth saying, not worth discarding.
            log.warning("[%s] %s did not take effect: %s",
                        recording.session, action, exc)
            events.emit("recorder.step_failed", session=recording.session,
                        action=action, target=step["target"], error=str(exc))
    recording.add(step)
    log.info("[%s] captured %s %s", recording.session, action, step["target"] or "")
    events.emit("recorder.step_captured", session=recording.session,
                scenario=recording.scenario_id, index=len(recording.steps),
                action=action, target=step["target"], value=step.get("value"))
    adapter.recorder_call("recorder.render(arg)", recording.state())


def _perform(adapter, action, selector, value):
    if action == "click":
        adapter.click(selector)
    elif action == "fill":
        adapter.fill(selector, value or "")
    elif action == "select":
        adapter.select(selector, value or "")
    elif action == "press":
        adapter.press_key(value or "")
    elif action == "goto":
        adapter.goto(selector)


def _finish(adapter, recording):
    """Stop capturing and write the scenario."""
    recording.finished = True
    recording.active = False
    result = recording.save()
    if result.get("ok"):
        log.info("[%s] wrote %s (%d steps)", recording.session,
                 result.get("path"), len(recording.steps))
    else:
        log.error("[%s] could not write %s: %s", recording.session,
                  recording.scenario_id, "; ".join(result.get("problems") or []))
    events.emit("recorder.finished", session=recording.session,
                scenario=recording.scenario_id, ok=bool(result.get("ok")),
                path=result.get("path"), steps=len(recording.steps),
                problems=list(result.get("problems") or []))
    adapter.recorder_call("recorder.stop()")


def _grammar():
    """The step vocabulary, handed to the page so its menu cannot drift."""
    from engine import compiler
    return {
        "selector_only": sorted(compiler.SELECTOR_ONLY),
        "selector_and_value": sorted(compiler.SELECTOR_AND_VALUE),
        "value_only": sorted(compiler.VALUE_ONLY),
        "url_target": sorted(compiler.URL_TARGET),
    }
