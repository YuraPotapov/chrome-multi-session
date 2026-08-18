"""Turning the launcher's two output channels into something the pages can show."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms_gui.runner import RunState, parse_log_line


# ------------------------------------------------------------------ log lines

def test_parses_the_launchers_console_format():
    record = parse_log_line("13:38:26  INFO    All 1 windows launched.")
    assert record["ts"] == "13:38:26"
    assert record["level"] == "INFO"
    assert record["session"] == ""
    assert record["text"] == "All 1 windows launched."


def test_picks_up_the_session_prefix_of_a_parallel_run():
    record = parse_log_line("13:38:27  WARNING [dev-agent]  Retry #1")
    assert record["session"] == "dev-agent"
    assert record["level"] == "WARNING"
    assert "Retry #1" in record["text"]


def test_an_unrecognisable_line_is_kept_whole():
    record = parse_log_line("Traceback (most recent call last):")
    assert record["text"] == "Traceback (most recent call last):"
    assert record["level"] == ""


# ------------------------------------------------------------------ run state

TREE = {"id": "0", "label": "smoke", "kind": "group", "children": [
    {"id": "0/0", "label": "goto", "kind": "step", "step_index": 0},
    {"id": "0/1", "label": "assert_visible", "kind": "step", "step_index": 1},
]}


def _feed(state, *events):
    for event in events:
        state.handle(event)


def test_a_window_appears_as_soon_as_it_launches(qapp):
    state = RunState()
    _feed(state, {"kind": "window.launched", "session": "dev-admin",
                  "login": "admin", "pid": 42})
    assert [s["name"] for s in state.ordered()] == ["dev-admin"]
    assert state.ordered()[0]["pid"] == 42
    assert state.ordered()[0]["state"] == "launched"


def test_sessions_keep_their_launch_order(qapp):
    state = RunState()
    _feed(state,
          {"kind": "window.launched", "session": "b", "pid": 2},
          {"kind": "window.launched", "session": "a", "pid": 1})
    assert [s["name"] for s in state.ordered()] == ["b", "a"]


def test_steps_progress_from_the_event_stream(qapp):
    state = RunState()
    _feed(state,
          {"kind": "session.attached", "session": "s", "login": "admin"},
          {"kind": "session.start", "session": "s", "scenarios": ["smoke"]},
          {"kind": "flow.start", "session": "s", "scenario": "smoke",
           "tree": TREE, "steps": 2},
          {"kind": "step.start", "session": "s", "index": 0},
          {"kind": "step.end", "session": "s", "index": 0, "status": "pass"})
    session = state.sessions["s"]
    assert session["state"] == "running"
    assert session["total"] == 2 and session["done"] == 1
    assert session["steps"][0]["status"] == "pass"
    assert session["tree"]["children"][1]["label"] == "assert_visible"


def test_a_failed_step_marks_the_session_failed_at_flow_end(qapp):
    state = RunState()
    _feed(state,
          {"kind": "flow.start", "session": "s", "scenario": "smoke",
           "tree": TREE, "steps": 2},
          {"kind": "step.end", "session": "s", "index": 0, "status": "fail",
           "message": "not visible"},
          {"kind": "flow.end", "session": "s", "status": "fail", "passed": 0,
           "total": 2})
    session = state.sessions["s"]
    assert session["state"] == "failed"
    assert session["steps"][0]["message"] == "not visible"
    assert session["flows"] == [{"scenario": "smoke", "status": "fail",
                                 "passed": 0, "total": 2}]


def test_a_second_scenario_resets_the_step_tally(qapp):
    state = RunState()
    _feed(state,
          {"kind": "flow.start", "session": "s", "scenario": "one", "tree": TREE,
           "steps": 2},
          {"kind": "step.end", "session": "s", "index": 0, "status": "pass"},
          {"kind": "flow.end", "session": "s", "status": "pass", "passed": 1,
           "total": 2},
          {"kind": "flow.start", "session": "s", "scenario": "two", "tree": TREE,
           "steps": 2})
    session = state.sessions["s"]
    assert session["scenario"] == "two" and session["done"] == 0
    assert len(session["flows"]) == 1     # the finished one is remembered


def test_attach_failure_is_visible_rather_than_silent(qapp):
    state = RunState()
    _feed(state, {"kind": "session.attach_failed", "session": "s", "login": "x"})
    assert state.sessions["s"]["state"] == "failed"


def test_run_dir_and_summary_are_captured(qapp):
    state = RunState()
    seen = []
    state.run_dir_known.connect(seen.append)
    _feed(state,
          {"kind": "run.dir", "dir": "/tmp/reports/20260813-120000"},
          {"kind": "run.summary", "passed": 2, "total": 3, "exit_code": 1})
    assert state.run_dir == "/tmp/reports/20260813-120000"
    assert seen == ["/tmp/reports/20260813-120000"]
    assert state.summary["passed"] == 2


def test_totals_add_up_across_sessions(qapp):
    state = RunState()
    for name in ("a", "b"):
        _feed(state, {"kind": "flow.start", "session": name, "scenario": "s",
                      "tree": TREE, "steps": 2},
              {"kind": "step.end", "session": name, "index": 0, "status": "pass"})
    assert state.totals() == (2, 4)


def test_an_unknown_event_kind_is_ignored(qapp):
    # A newer core may emit things this GUI has never heard of.
    state = RunState()
    _feed(state, {"kind": "something.new", "session": "s", "detail": 1})
    assert state.ordered() == []


def test_the_worker_count_in_force_comes_off_the_event_stream(qapp):
    state = RunState()
    assert state.workers is None            # a fixed number reports nothing
    _feed(state, {"kind": "governor.limit", "limit": 7, "ceiling": 7,
                  "unit": "sessions", "why": "starting at one per core"})
    assert state.workers["limit"] == 7
    _feed(state, {"kind": "governor.limit", "limit": 6, "ceiling": 7,
                  "unit": "sessions", "why": "cpu at 96%"})
    # The number in force AND why it moved: a count that drops with no reason
    # given is the thing that made the old throttle look broken.
    assert (state.workers["limit"], state.workers["why"]) == (6, "cpu at 96%")


def test_a_new_run_forgets_the_last_run_s_worker_count(qapp):
    state = RunState()
    _feed(state, {"kind": "governor.limit", "limit": 3, "ceiling": 7})
    state.reset()
    assert state.workers is None


def test_a_session_asked_to_stop_says_so_before_the_core_answers(qapp):
    state = RunState()
    _feed(state, {"kind": "window.launched", "session": "dev-agent", "pid": 1})
    state.mark_stopping("dev-agent")
    # The core has to finish the step it is in before it can reply, and a menu
    # entry that looks like it did nothing invites a second click.
    assert state.sessions["dev-agent"]["state"] == "stopping"


def test_the_core_announcing_a_stop_marks_that_session_only(qapp):
    state = RunState()
    _feed(state,
          {"kind": "window.launched", "session": "a", "pid": 1},
          {"kind": "window.launched", "session": "b", "pid": 2},
          {"kind": "session.stopping", "session": "a"})
    assert state.sessions["a"]["state"] == "stopping"
    assert state.sessions["b"]["state"] == "launched"


def _flow(state, name, scenario, labels, status="pass"):
    tree = {"kind": "flow", "label": scenario,
            "children": [{"kind": "step", "step_index": i, "label": l}
                         for i, l in enumerate(labels)]}
    _feed(state, {"kind": "flow.start", "session": name, "scenario": scenario,
                  "tree": tree, "steps": len(labels)})
    for i, _l in enumerate(labels):
        _feed(state, {"kind": "step.start", "session": name, "index": i},
              {"kind": "step.end", "session": name, "index": i, "status": "pass"})
    _feed(state, {"kind": "flow.end", "session": name, "status": status,
                  "passed": len(labels), "total": len(labels)})


def test_every_scenario_a_session_runs_is_kept(qapp):
    # The Run page could only ever show the last scenario, because flow.start
    # overwrote the tree and the steps. The in-page overlay always showed the
    # whole list; this is the state that lets the page match it.
    state = RunState()
    _feed(state, {"kind": "window.launched", "session": "s", "pid": 1},
          {"kind": "session.start", "session": "s",
           "scenarios": ["access_agent", "multicompany_agent"]})
    _flow(state, "s", "access_agent", ["click a", "click b"])
    _flow(state, "s", "multicompany_agent", ["click c"], status="fail")
    runs = state.sessions["s"]["runs"]
    assert list(runs) == ["access_agent", "multicompany_agent"]   # in run order
    assert runs["access_agent"]["status"] == "pass"
    assert runs["multicompany_agent"]["status"] == "fail"
    # And the earlier scenario kept its own steps rather than the later one's.
    assert len(runs["access_agent"]["tree"]["children"]) == 2
    assert len(runs["multicompany_agent"]["tree"]["children"]) == 1


def test_a_finished_scenario_keeps_its_step_marks(qapp):
    state = RunState()
    _feed(state, {"kind": "window.launched", "session": "s", "pid": 1})
    _flow(state, "s", "first", ["a", "b"])
    _flow(state, "s", "second", ["c"])
    first = state.sessions["s"]["runs"]["first"]
    assert [first["steps"][i]["status"] for i in (0, 1)] == ["pass", "pass"]
    assert first["done"] == 2 and first["total"] == 2


def test_a_session_that_failed_once_does_not_report_pass(qapp):
    # The tag said PASS beside a tree with a red mark in it, because flow.end
    # took the latest outcome instead of the worst one.
    state = RunState()
    _feed(state, {"kind": "window.launched", "session": "s", "pid": 1})
    _flow(state, "s", "first", ["a"], status="fail")
    _flow(state, "s", "second", ["b"], status="pass")
    assert state.sessions["s"]["state"] == "failed"


def test_a_session_where_everything_passed_reports_pass(qapp):
    state = RunState()
    _feed(state, {"kind": "window.launched", "session": "s", "pid": 1})
    _flow(state, "s", "first", ["a"])
    _flow(state, "s", "second", ["b"])
    assert state.sessions["s"]["state"] == "passed"


def test_the_scenarios_finishing_is_not_the_launcher_finishing(qapp):
    # Without --close-after the launcher stays up holding the windows open, so
    # the page waited for a process exit that was minutes away and kept saying
    # RUNNING with a ticking clock.
    state = RunState()
    assert state.flows_finished is False
    _feed(state, {"kind": "run.finished", "exit_code": 1})
    assert state.flows_finished is True and state.exit_code == 1
    state.reset()
    assert state.flows_finished is False
