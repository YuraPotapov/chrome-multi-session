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
