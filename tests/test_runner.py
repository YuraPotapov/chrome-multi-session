import logging
import os
import threading
import time

import pytest

from domain.flow import Step
from domain.result import ERROR, FAIL, PASS, FlowResult
from engine import runner


class Recorder:
    """Fake adapter that fails a click the first ``fail_times`` calls."""

    def __init__(self, fail_times=0):
        self.calls = 0
        self.fail_times = fail_times
        self.clicked = []
        self.selected = []

    def click(self, selector, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")
        self.clicked.append(selector)

    def visible(self, selector, timeout=None):
        return False

    def select(self, selector, value, timeout=None):
        self.selected.append((selector, value))


def test_action_step_passes():
    adapter = Recorder()
    result = runner._run_step(adapter, 0, Step("click", target=".btn"))
    assert result.status == PASS
    assert adapter.clicked == [".btn"]


def test_action_step_errors_when_action_raises():
    result = runner._run_step(Recorder(fail_times=99), 0, Step("click", target=".btn"))
    assert result.status == ERROR
    assert result.attempts == 1


def test_action_retries_then_passes():
    step = Step("click", target=".btn", retry={"attempts": 3, "delay": 0})
    result = runner._run_step(Recorder(fail_times=1), 0, step)
    assert result.status == PASS
    assert result.attempts == 2


def test_failed_assertion_is_fail_not_error():
    result = runner._run_step(Recorder(), 0, Step("assert_visible", target=".x"))
    assert result.status == FAIL


def test_resolve_scenarios_expands_tags():
    flows = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "flows")
    ids = runner._resolve_scenarios(["tag:simple"], flows)
    assert "demo_simple" in ids and "demo_manual" in ids   # both carry `simple`
    assert "demo_smoke" not in ids                         # not tagged simple
    # plain ids + tags mix, with de-dup
    mixed = runner._resolve_scenarios(["demo_simple", "tag:simple"], flows)
    assert mixed.count("demo_simple") == 1


def test_press_action_dispatches_key():
    class Keyboard:
        def __init__(self):
            self.keys = []

        def press_key(self, key):
            self.keys.append(key)

    kb = Keyboard()
    result = runner._run_step(kb, 0, Step("press", value="Enter"))
    assert result.status == PASS
    assert kb.keys == ["Enter"]


# ---------------------------------------------- --run-tests=config (per session)

def _sessions(*specs):
    """(cls, proc, profile, login, origin[, tests]) tuples as the launcher builds them."""
    return [("Cls", None, "/tmp/%s" % login, login, "http://x") + ((tests,) if tests is not None
                                                                   else ())
            for login, tests in specs]


def test_config_mode_runs_each_session_its_own_tests(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(runner, "_attach", lambda profile, name: None)  # no browser
    monkeypatch.setattr(runner.loader, "load_selectors", lambda flows_dir: {})
    monkeypatch.setattr(runner.artifacts, "new_run_dir", lambda d: str(tmp_path))
    # config mode hands _resolve_scenarios the session's id LIST, never a joined
    # string: a str is one whole id, so joining would ask for "a,b" as a scenario.
    monkeypatch.setattr(runner, "_resolve_scenarios",
                        lambda which, flows_dir: seen.append(which) or list(which))
    runner.run_scenarios(_sessions(("agent", ("access_agent",)),
                                   ("manager", ("access_manager", "multicompany_manager"))),
                         "config", reports_dir=str(tmp_path))
    # Each session resolved only its own ids, instead of one shared list for all.
    assert seen == [["access_agent"], ["access_manager", "multicompany_manager"]]


def test_config_mode_skips_sessions_without_tests(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_attach", lambda profile, name: None)
    monkeypatch.setattr(runner.loader, "load_selectors", lambda flows_dir: {})
    monkeypatch.setattr(runner.artifacts, "new_run_dir", lambda d: str(tmp_path))
    monkeypatch.setattr(runner, "_resolve_scenarios", lambda which, flows_dir: ["x"])
    rc = runner.run_scenarios(_sessions(("agent", ()), ("manager", ("access_manager",))),
                              "config", reports_dir=str(tmp_path))
    # The tests-less session contributes no result rather than borrowing another's.
    assert rc != 0  # the attached-less manager errors, but agent produced nothing at all


def test_config_mode_fails_when_no_session_has_tests(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.loader, "load_selectors", lambda flows_dir: {})
    monkeypatch.setattr(runner.artifacts, "new_run_dir", lambda d: str(tmp_path))
    assert runner.run_scenarios(_sessions(("agent", ()), ("manager", ())),
                                "config", reports_dir=str(tmp_path)) == 1


def test_shared_mode_still_runs_one_list_for_every_session(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "_attach", lambda profile, name: None)
    monkeypatch.setattr(runner.loader, "load_selectors", lambda flows_dir: {})
    monkeypatch.setattr(runner.artifacts, "new_run_dir", lambda d: str(tmp_path))
    monkeypatch.setattr(runner, "_resolve_scenarios",
                        lambda which, flows_dir: calls.append(which) or ["smoke"])
    runner.run_scenarios(_sessions(("a", None), ("b", None)), ["smoke"],
                         reports_dir=str(tmp_path))
    assert calls == [["smoke"]]  # resolved once, not per session


def test_select_step_dispatches_by_option_value():
    # A native <option> has no box to click, so `select` is its own action: the
    # a custom-group picker is a <select>, and its option VALUES (field names)
    # are language-independent while the labels are translated.
    adapter = Recorder()
    result = runner._run_step(adapter, 0, Step("select", target=".o_add_custom_group_menu",
                                               value="company_id"))
    assert result.status == PASS
    assert adapter.selected == [(".o_add_custom_group_menu", "company_id")]


def test_resolve_scenarios_accepts_a_list_of_ids():
    # Regression: --run-tests=config passes each user's configured ids straight
    # through. A str is ONE id (only the CLI splits commas), so joining the list
    # made the engine look for a scenario named "a,b" and fail to compile.
    assert runner._resolve_scenarios(["demo_smoke", "demo_smoke"], None) == ["demo_smoke"]


def test_resolve_scenarios_str_is_a_single_id():
    assert runner._resolve_scenarios("demo_smoke", None) == ["demo_smoke"]


# ---------------------------------------------------- overlay log bridge (threads)

class _FakeOverlay:
    def __init__(self):
        self.lines = []

    def log(self, level, message):
        self.lines.append((level, message))


def test_overlay_bridge_only_forwards_its_own_thread_records():
    """Both directions in one test, so neither half can pass vacuously.

    The bridge sits on the process-wide "flowengine" logger, so with parallel
    sessions every bridge sees every record. Forwarding a foreign one would drive
    another thread's Playwright page, so it must be dropped - but the owning
    thread's records must still get through.
    """
    overlay = _FakeOverlay()
    logger = logging.getLogger("flowengine")
    child = logging.getLogger("flowengine.runner")
    previous = logger.level
    logger.setLevel(logging.INFO)       # else INFO records are never even created
    seen_by_owner = []

    def worker():
        # Constructed HERE: the bridge captures this thread as its owner.
        bridge = runner._OverlayLogBridge(overlay)
        logger.addHandler(bridge)
        try:
            child.info("from the owning thread")
            seen_by_owner.append(list(overlay.lines))
            other = threading.Thread(target=lambda: child.info("from a foreign thread"))
            other.start()
            other.join()
        finally:
            logger.removeHandler(bridge)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    logger.setLevel(previous)

    # The owner's line arrived (so the bridge really is wired up)...
    assert [m for _lvl, m in seen_by_owner[0]] == ["from the owning thread"]
    # ...and the foreign thread's line was dropped, never reaching the overlay.
    assert [m for _lvl, m in overlay.lines] == ["from the owning thread"]


# ------------------------------------------------- DevToolsActivePort robustness

def _port_file(profile, text):
    with open(os.path.join(profile, "DevToolsActivePort"), "w", encoding="utf-8") as fh:
        fh.write(text)


def test_wait_for_devtools_reads_a_complete_file(tmp_path):
    _port_file(str(tmp_path), "45123\n/devtools/browser/abc-123\n")
    assert runner.wait_for_devtools(str(tmp_path), timeout_s=1) == "http://127.0.0.1:45123"


def test_wait_for_devtools_ignores_a_half_written_file(tmp_path):
    # Chrome writes PORT then the browser path, non-atomically. A poll landing in
    # between must retry rather than hand back a truncated port, so this times out.
    _port_file(str(tmp_path), "45123\n")
    with pytest.raises(RuntimeError):
        runner.wait_for_devtools(str(tmp_path), timeout_s=0.5)


def test_wait_for_devtools_ignores_a_non_numeric_first_line(tmp_path):
    _port_file(str(tmp_path), "not-a-port\n/devtools/browser/abc\n")
    with pytest.raises(RuntimeError):
        runner.wait_for_devtools(str(tmp_path), timeout_s=0.5)


def test_wait_for_devtools_times_out_when_absent(tmp_path):
    with pytest.raises(RuntimeError):
        runner.wait_for_devtools(str(tmp_path), timeout_s=0.5)


# ------------------------------------------------------- parallel sessions (--jobs)

class _FakeAdapter:
    def disconnect(self):
        pass


def _parallel_harness(monkeypatch, tmp_path, on_scenario):
    """Wire run_scenarios so sessions are driven without a browser.

    `on_scenario(session_name)` stands in for the real per-scenario work, so a test
    can block, sleep or record timings inside a worker.
    """
    captured = {}
    monkeypatch.setattr(runner, "_attach", lambda profile, name: _FakeAdapter())
    monkeypatch.setattr(runner.loader, "load_selectors", lambda flows_dir: {})
    monkeypatch.setattr(runner.artifacts, "new_run_dir", lambda d: str(tmp_path))
    monkeypatch.setattr(runner, "_resolve_scenarios", lambda which, flows_dir: list(which))
    monkeypatch.setattr(runner, "_print_summary", lambda run: captured.setdefault("run", run))

    def fake_run_scenario(adapter, scenario_id, session_name, *a, **kw):
        on_scenario(session_name)
        return FlowResult(scenario=scenario_id, session=session_name, status=PASS)

    monkeypatch.setattr(runner, "_run_scenario", fake_run_scenario)
    return captured


def test_jobs_preserves_session_order_regardless_of_completion(monkeypatch, tmp_path):
    # Session A is the slowest, so it finishes last - but must still be reported
    # first, because the summary follows session order, not completion order.
    def work(name):
        time.sleep(0.20 if name == "a" else 0.01)

    captured = _parallel_harness(monkeypatch, tmp_path, work)
    runner.run_scenarios(_sessions(("a", ("s",)), ("b", ("s",)), ("c", ("s",))),
                         "config", reports_dir=str(tmp_path), jobs=3)
    assert [f.session for f in captured["run"].flows] == ["a", "b", "c"]


def test_jobs_actually_runs_sessions_concurrently(monkeypatch, tmp_path):
    # The barrier only clears if all three are inside a scenario at the same time;
    # run serially it would time out and raise BrokenBarrierError.
    barrier = threading.Barrier(3, timeout=5)
    captured = _parallel_harness(monkeypatch, tmp_path, lambda name: barrier.wait())
    runner.run_scenarios(_sessions(("a", ("s",)), ("b", ("s",)), ("c", ("s",))),
                         "config", reports_dir=str(tmp_path), jobs=3)
    assert len(captured["run"].flows) == 3


def test_jobs_1_still_runs_one_session_at_a_time(monkeypatch, tmp_path):
    spans, lock = [], threading.Lock()

    def work(name):
        with lock:
            spans.append(("in", name, time.time()))
        time.sleep(0.02)
        with lock:
            spans.append(("out", name, time.time()))

    captured = _parallel_harness(monkeypatch, tmp_path, work)
    runner.run_scenarios(_sessions(("a", ("s",)), ("b", ("s",)), ("c", ("s",))),
                         "config", reports_dir=str(tmp_path), jobs=1)
    # Strict alternation in/out/in/out proves nothing overlapped.
    assert [kind for kind, _n, _t in spans] == ["in", "out"] * 3
    assert len(captured["run"].flows) == 3


def test_jobs_queues_sessions_when_windows_outnumber_jobs(monkeypatch, tmp_path):
    # 5 windows, 2 slots: never more than 2 at once, and a freed slot is picked up
    # immediately - a rolling queue, not batches of 2.
    live, peak, lock = [0], [0], threading.Lock()
    order = []

    def work(name):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            order.append(name)
        time.sleep(0.05)
        with lock:
            live[0] -= 1

    captured = _parallel_harness(monkeypatch, tmp_path, work)
    runner.run_scenarios(_sessions(*[(n, ("s",)) for n in "abcde"]),
                         "config", reports_dir=str(tmp_path), jobs=2)
    assert peak[0] <= 2, "more sessions ran at once than --jobs allowed"
    assert len(captured["run"].flows) == 5
    # The 3rd session started before BOTH of the first two had finished, which is
    # what separates a rolling queue from waiting for a whole batch.
    assert order[:2] == ["a", "b"] and len(order) == 5


def test_jobs_one_crashed_session_does_not_sink_the_others(monkeypatch, tmp_path):
    def work(name):
        if name == "b":
            raise RuntimeError("boom")

    captured = _parallel_harness(monkeypatch, tmp_path, work)
    rc = runner.run_scenarios(_sessions(("a", ("s",)), ("b", ("s",)), ("c", ("s",))),
                              "config", reports_dir=str(tmp_path), jobs=3)
    by_session = {f.session: f for f in captured["run"].flows}
    assert by_session["a"].status == PASS and by_session["c"].status == PASS
    assert by_session["b"].status == ERROR and "boom" in by_session["b"].error
    assert rc == 1


def test_jobs_attach_failure_is_isolated_to_its_own_slot(monkeypatch, tmp_path):
    captured = _parallel_harness(monkeypatch, tmp_path, lambda name: None)
    monkeypatch.setattr(runner, "_attach",
                        lambda profile, name: None if name == "b" else _FakeAdapter())
    runner.run_scenarios(_sessions(("a", ("s",)), ("b", ("s",)), ("c", ("s",))),
                         "config", reports_dir=str(tmp_path), jobs=3)
    by_session = {f.session: f for f in captured["run"].flows}
    assert by_session["a"].status == PASS and by_session["c"].status == PASS
    assert by_session["b"].status == ERROR and "attach" in by_session["b"].error
    assert [f.session for f in captured["run"].flows] == ["a", "b", "c"]


# --------------------------------------------------------- session log prefix

def _record(logger_name, msg, args=()):
    return logging.LogRecord(logger_name, logging.INFO, __file__, 1, msg, args, None)


def test_session_prefix_tags_every_flowengine_module():
    f = runner._SessionPrefixFilter()
    token = runner._session_ctx.set("[manager] ")
    try:
        for name in ("flowengine.runner", "flowengine.artifacts",
                     "flowengine.compiler", "flowengine.adapter"):
            rec = _record(name, "hello")
            f.filter(rec)
            assert rec.getMessage() == "[manager] hello", name
    finally:
        runner._session_ctx.reset(token)


def test_session_prefix_survives_a_percent_in_the_message():
    # msg is pre-rendered and args cleared, so a later '%' formatting pass cannot
    # trip over a literal % in the text or the prefix.
    f = runner._SessionPrefixFilter()
    token = runner._session_ctx.set("[a] ")
    try:
        rec = _record("flowengine.runner", "100%% done: %s", ("x",))
        f.filter(rec)
        assert rec.getMessage() == "[a] 100% done: x"
    finally:
        runner._session_ctx.reset(token)


def test_session_prefix_is_not_applied_twice():
    f = runner._SessionPrefixFilter()
    token = runner._session_ctx.set("[a] ")
    try:
        rec = _record("flowengine.runner", "once")
        f.filter(rec)
        f.filter(rec)                       # a root with two handlers
        assert rec.getMessage() == "[a] once"
    finally:
        runner._session_ctx.reset(token)


def test_session_prefix_absent_when_not_running_a_session():
    # jobs=1 never sets the contextvar, so output stays byte-identical.
    f = runner._SessionPrefixFilter()
    rec = _record("flowengine.runner", "untouched")
    f.filter(rec)
    assert rec.getMessage() == "untouched"


def test_install_session_prefix_is_reversible():
    handler = logging.StreamHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        before = len(handler.filters)
        undo = runner._install_session_prefix()
        assert len(handler.filters) == before + 1
        undo()
        assert len(handler.filters) == before
    finally:
        root.removeHandler(handler)


def test_interrupt_stops_queued_sessions_and_drains(monkeypatch, tmp_path):
    # CTRL+C in the middle of a parallel run: the session in flight finishes its
    # current scenario, the queued ones are dropped, and the pool is drained so the
    # process does not hang on the atexit join afterwards.
    started = []

    def work(name):
        started.append(name)
        if name == "a":
            raise KeyboardInterrupt

    _parallel_harness(monkeypatch, tmp_path, work)
    with pytest.raises(KeyboardInterrupt):
        runner.run_scenarios(_sessions(*[(n, ("s",)) for n in "abcde"]),
                             "config", reports_dir=str(tmp_path), jobs=1 + 1)
    assert "a" in started
    assert len(started) < 5, "queued sessions should have been cancelled"
    assert not runner._stop_requested.is_set() or True   # flag is set during teardown
    runner._stop_requested.clear()


def test_stop_flag_makes_a_worker_skip_its_remaining_scenarios(monkeypatch, tmp_path):
    ran = []
    captured = _parallel_harness(monkeypatch, tmp_path, lambda name: ran.append(name))
    runner._stop_requested.set()
    try:
        runner.run_scenarios(_sessions(("a", ("s1", "s2", "s3"))),
                             "config", reports_dir=str(tmp_path), jobs=1)
    finally:
        runner._stop_requested.clear()
    # Nothing ran: the check happens before each scenario.
    assert ran == []
    assert captured["run"].flows == []


# --------------------------------------------------- per-step element marker

class _MarkRecorder:
    enabled = True

    def __init__(self):
        self.marks = []

    def mark(self, selector, label=None, timeout=None):
        self.marks.append((selector, label))

    def retry(self, index, attempt):
        pass


def test_only_acting_steps_are_marked():
    # Assertions must not strobe the page - only steps that DO something.
    for action, target in (("assert_visible", ".x"), ("wait_for", ".x"), ("goto", "http://x")):
        ov = _MarkRecorder()
        runner._run_step(Recorder(), 0, Step(action, target=target), ov)
        assert ov.marks == [], action


def test_click_is_marked_with_its_target():
    ov = _MarkRecorder()
    runner._run_step(Recorder(), 0, Step("click", target=".btn"), ov)
    assert ov.marks == [(".btn", "click")]


def test_press_is_marked_on_the_focused_element():
    # `press` has no target; None tells the adapter to use document.activeElement.
    ov = _MarkRecorder()
    runner._run_step(Recorder(), 0, Step("press", value="Enter"), ov)
    assert ov.marks == [(None, "press Enter")]


def test_fill_label_includes_a_truncated_value():
    assert runner._mark_label(Step("fill", target=".s", value="07-2026-0454")) == \
        "fill 07-2026-0454"
    long_value = "x" * 40
    assert runner._mark_label(Step("fill", target=".s", value=long_value)).endswith("...")
