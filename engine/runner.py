"""Execution engine: attach to each launched session and run the scenarios.

Entry point :func:`run_scenarios` is what ``session_launcher.py`` calls when
``--run-tests`` is given. For each launched Chrome it reads the profile's
``DevToolsActivePort``, attaches Playwright over CDP (the auto-login extension
has already signed in by then), compiles each scenario, executes it step by step
with per-step retries, captures artifacts on failure, and writes a report.
Returns a process exit code (0 = all passed).
"""

import concurrent.futures
import contextvars
import logging
import os
import threading
import time

import runtime_paths

from domain.result import FlowResult, RunResult, StepResult, PASS, FAIL, ERROR
from engine import artifacts, assertions, compiler, events, loader
from engine.context import RunContext
from engine.overlay import ExecutionOverlay, NullOverlay

log = logging.getLogger("flowengine.runner")


class _OverlayLogBridge(logging.Handler):
    """Forwards ``flowengine.*`` log records into ONE session's overlay Logs widget.

    Kept at INFO so DEBUG chatter (including the adapter's own overlay-render
    diagnostics) is never forwarded - that also rules out any feedback loop.

    MUST be constructed on the thread that owns its overlay: it records that
    thread's id and ignores records from any other. Handlers live on the shared
    process-wide "flowengine" logger, so with several sessions running at once
    every bridge would otherwise see every session's records - which is not just
    noise in the wrong HUD. `emit` reaches `page.evaluate` through
    overlay.log -> _push -> adapter.overlay_render, so a foreign record would have
    one thread synchronously driving ANOTHER thread's Playwright page: a
    cross-thread greenlet switch, silently eaten by the excepts below.
    """

    def __init__(self, overlay):
        super().__init__(level=logging.INFO)
        self._overlay = overlay
        self._owner_tid = threading.get_ident()

    def filter(self, record):
        # Handlers run inline in the emitting thread, so this identifies the
        # session that produced the record. Filtering here rather than in emit()
        # matters: Handler.handle() calls filter() BEFORE acquire(), so a foreign
        # record never even takes this handler's lock.
        return threading.get_ident() == self._owner_tid and super().filter(record)

    def emit(self, record):
        try:
            self._overlay.log(record.levelname, record.getMessage())
        except Exception:  # logging must never break a run
            pass


# Which session the current thread is driving, for the console log prefix. Set in
# the worker, so the main thread's own lines (Reports ->, the run summary, and
# everything the launcher logs) stay unprefixed.
_session_ctx = contextvars.ContextVar("flowengine_session", default=None)

# Set on CTRL+C during a parallel run. Workers check it BETWEEN scenarios, so an
# interrupted run stops after the current step's timeout instead of grinding
# through every remaining scenario in every running window.
_stop_requested = threading.Event()


class _SessionPrefixFilter(logging.Filter):
    """Tags each console line with the session that produced it, while parallel.

    Installed on the ROOT handler, not on the "flowengine" logger: a filter on a
    logger only sees records logged through THAT logger, never ones propagating up
    from flowengine.runner/.artifacts/.compiler/.adapter. Handlers are where
    propagated records actually arrive, so this is the one place that catches all
    four with no per-module wiring.
    """

    def filter(self, record):
        prefix = _session_ctx.get()
        if prefix and not getattr(record, "_session_tagged", False):
            record._session_tagged = True   # a root with two handlers must not double-tag
            record.msg = prefix + record.getMessage()
            record.args = ()                # pre-rendered, so a '%' in a name is inert
        return True


def _install_session_prefix():
    """Add the prefix filter to every root handler; returns an undo callable."""
    handlers = list(logging.getLogger().handlers)
    prefix_filter = _SessionPrefixFilter()
    for handler in handlers:
        handler.addFilter(prefix_filter)

    def remove():
        for handler in handlers:
            handler.removeFilter(prefix_filter)
    return remove


def _make_overlay(overlay_components, adapter, session_name=None):
    """The observer the runner notifies at every execution transition.

    Two independent consumers can want the same transitions: the in-page HUD
    (``--execution-overlay``) and the JSONL event stream (``--events``). When
    both are on they are wrapped in a :class:`events.Tee` so the runner keeps
    its single ``overlay.*`` call site; when neither is, this is a no-op object
    and nothing downstream has to test for it.
    """
    observers = []
    if overlay_components:
        observers.append(ExecutionOverlay(overlay_components, adapter))
    if events.enabled():
        observers.append(events.EventObserver(session_name))
    if not observers:
        return NullOverlay()
    if len(observers) == 1:
        return observers[0]
    return events.Tee(observers)

DEFAULT_TIMEOUT_MS = 30000        # generous: covers the post-login readiness gate
DEVTOOLS_WAIT_S = 30              # how long to wait for Chrome to open its debug port
DEFAULT_REPORTS_DIR = runtime_paths.reports_dir()


def run_scenarios(sessions, which, env=None, flows_dir=None, reports_dir=None,
                  overlay_components=None, report=None, jobs=1):
    """Run ``which`` scenarios against every launched ``sessions`` entry.

    ``sessions`` is a list of ``(cls, proc, profile, login, origin[, tests])``
    tuples from the launcher. ``which`` is ``"all"``, a list of scenario ids, or
    ``"config"`` - the last meaning each session runs its OWN ``tests`` (the
    config's per-user field), so one launch can cover several roles without every
    scenario being replayed against every window.
    ``overlay_components`` (from ``--execution-overlay``) enables the in-page HUD.
    ``report`` (from ``--report-*``) is a :class:`artifacts.ReportConfig`; ``None``
    means the legacy default (result.json on success, full bundle on failure).

    ``jobs`` (from ``--jobs``) is how many WINDOWS may be driven at once; 1 keeps
    the original one-window-at-a-time behaviour byte for byte. Scenarios inside a
    window always run one after another - a single page cannot be driven twice -
    so this only removes the wait between windows. With more windows than jobs the
    extras queue and start as soon as a slot frees.
    """
    env = env or {}
    flows_dir = flows_dir or loader.DEFAULT_FLOWS_DIR
    reports_dir = reports_dir or DEFAULT_REPORTS_DIR
    report = report or artifacts.ReportConfig()
    selectors = loader.load_selectors(flows_dir)
    per_session = which == "config"
    shared = [] if per_session else _resolve_scenarios(which, flows_dir)
    if not per_session and not shared:
        log.error("No scenarios to run.")
        return 1

    run = RunResult()
    run_dir = artifacts.new_run_dir(reports_dir)
    log.info("Reports -> %s", run_dir)
    events.emit("run.dir", dir=run_dir)

    # Results are collected per session POSITION, not appended as they finish, so
    # the summary lists sessions in the order given no matter what order they
    # complete in.
    slots = [[] for _ in sessions]
    workers = max(1, min(int(jobs), len(sessions) or 1))
    plan = _plan_sessions(sessions, per_session, shared, flows_dir, slots)

    def drive(session, session_name, scenarios, log_prefix=None):
        return _run_one_session(session, session_name, scenarios, env, flows_dir,
                                selectors, run_dir, overlay_components, report,
                                log_prefix=log_prefix)

    if workers == 1:
        # Lazy consumption keeps the planner's warnings interleaved with the runs,
        # exactly as before there was any notion of workers. No prefix filter is
        # installed either, so single-session output is byte-identical to before.
        ran_any = False
        for index, session, session_name, scenarios in plan:
            ran_any = True
            slots[index] = drive(session, session_name, scenarios)
    else:
        planned = list(plan)
        ran_any = bool(planned)
        log.info("Running %d session(s), %d at a time.", len(planned), workers)
        # Lines from N windows interleave, so tag each with its session. Padding
        # sits OUTSIDE the brackets so a fixed-string grep still matches.
        width = max((len(name) for _i, _s, name, _sc in planned), default=0)
        prefixes = {i: ("[%s]" % name).ljust(width + 3) for i, _s, name, _sc in planned}
        undo_prefix = _install_session_prefix()
        _stop_requested.clear()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                                     thread_name_prefix="session")
        try:
            futures = [(index, session_name,
                        pool.submit(drive, session, session_name, scenarios, prefixes[index]))
                       for index, session, session_name, scenarios in planned]
            # Joined in submit order so the log of completions is deterministic;
            # slots keep session order regardless of who finishes first.
            for index, session_name, future in futures:
                try:
                    slots[index] = future.result()
                except concurrent.futures.CancelledError:
                    slots[index] = []          # never started: CTRL+C dropped it
                except KeyboardInterrupt:
                    raise
                except BaseException as exc:      # noqa: BLE001 - one window must not sink the run
                    log.error("--- session %s crashed: %s: %s ---",
                              session_name, type(exc).__name__, exc)
                    slots[index] = [FlowResult(scenario=sid, session=session_name, status=ERROR,
                                               error="session crashed: %s: %s"
                                                     % (type(exc).__name__, exc))
                                    for sid in _scenarios_of(planned, index)]
        except KeyboardInterrupt:
            # Queued sessions are dropped outright; running ones stop after the
            # step they are in (bounded by that step's timeout, 30 s by default).
            _stop_requested.set()
            pool.shutdown(wait=False, cancel_futures=True)
            running = sum(1 for _i, _n, f in futures if f.running())
            log.warning("Interrupted - waiting for %d running session(s) to finish the "
                        "current step...", running)
            raise
        finally:
            # Always drain here: an undrained pool would otherwise be joined by
            # concurrent.futures' atexit hook, hanging the process AFTER the
            # traceback with nothing on screen to explain it.
            pool.shutdown(wait=True)
            undo_prefix()

    if per_session and not ran_any:
        log.error("No session had any 'tests' configured (--run-tests=config).")
        return 1
    for results in slots:
        run.flows.extend(results)
    _print_summary(run)
    return run.exit_code


def _scenarios_of(planned, index):
    """The scenario ids planned for a session position (for crash bookkeeping)."""
    for i, _session, _name, scenarios in planned:
        if i == index:
            return scenarios
    return []


def _plan_sessions(sessions, per_session, shared, flows_dir, slots):
    """Yield ``(index, session, session_name, scenarios)`` for runnable sessions.

    Deliberately a GENERATOR consumed on the caller's thread: scenario resolution
    stays on the main thread in session order, and the "no tests configured"
    warning is emitted between runs rather than hoisted ahead of all of them.
    Sessions that resolve to nothing write their own ERROR result into ``slots``
    and are not yielded.
    """
    for index, session in enumerate(sessions):
        profile = session[2]
        session_name = os.path.basename(profile.rstrip("/\\"))
        if per_session:
            # session[5] is that user's configured "tests"; a user without any is
            # skipped rather than silently inheriting another role's scenarios.
            configured = list(session[5]) if len(session) > 5 else []
            if not configured:
                log.warning("--- session %s: no 'tests' configured, skipping ---", session_name)
                continue
            # Pass the LIST: _resolve_scenarios treats a str as one whole id (only
            # the CLI splits on commas), so joining these would look for a single
            # scenario literally named "access_manager,multicompany_manager".
            scenarios = _resolve_scenarios(configured, flows_dir)
            if not scenarios:
                log.error("--- session %s: 'tests' %s matched no scenario ---",
                          session_name, ", ".join(configured))
                slots[index] = [FlowResult(scenario=",".join(configured), session=session_name,
                                           status=ERROR, error="no scenario matched 'tests'")]
                continue
        else:
            scenarios = shared
        yield index, session, session_name, scenarios


def _run_one_session(session, session_name, scenarios, env, flows_dir, selectors,
                     run_dir, overlay_components, report, log_prefix=None):
    """Drive ONE window: attach, run its scenarios, disconnect. Returns its results.

    Everything here - the CDP attach, every step, every screenshot and the
    disconnect - must happen on ONE thread, because Playwright's sync API is
    bound to the thread that created its instance. That is why this whole function
    is the unit of work handed to a worker, and why the overlay and its log bridge
    are constructed inside it: _OverlayLogBridge records the thread it was built
    on and drops records from any other.

    Returns its own list rather than appending to a shared one, so running several
    of these at once needs no lock at all.
    """
    results = []
    cls, _proc, profile, login, origin = session[:5]
    token = _session_ctx.set(log_prefix) if log_prefix else None
    try:
        return _drive_session(session, session_name, scenarios, env, flows_dir,
                              selectors, run_dir, overlay_components, report, results)
    finally:
        if token is not None:
            _session_ctx.reset(token)


def _drive_session(session, session_name, scenarios, env, flows_dir, selectors,
                   run_dir, overlay_components, report, results):
    cls, _proc, profile, login, origin = session[:5]
    log.info("--- session %s: %s ---", session_name, ", ".join(scenarios))
    adapter = _attach(profile, session_name)
    if adapter is None:
        events.emit("session.attach_failed", session=session_name, login=login,
                    profile=profile, scenarios=list(scenarios))
        return [FlowResult(scenario=scenario_id, session=session_name,
                           status=ERROR, error="could not attach over CDP")
                for scenario_id in scenarios]
    events.emit("session.attached", session=session_name, login=login,
                cls=cls, profile=profile, origin=origin)
    ctx = RunContext(user={"login": login, "class": cls},
                     env={"origin": origin, "url": env.get("url")})
    overlay = _make_overlay(overlay_components, adapter, session_name)
    bridge = None
    if overlay_components:
        # Feeds the HUD's Logs widget only - the event stream's consumer already
        # receives every log record on stderr.
        bridge = _OverlayLogBridge(overlay)
        logging.getLogger("flowengine").addHandler(bridge)
    # Show the whole plan up front, so a finished or failed scenario stays visible
    # when the next one starts instead of being replaced by it.
    overlay.session_start(scenarios)
    try:
        for scenario_id in scenarios:
            if _stop_requested.is_set():
                log.warning("Interrupted: skipping %s", scenario_id)
                break
            results.append(
                _run_scenario(adapter, scenario_id, session_name, flows_dir,
                              selectors, ctx, run_dir, overlay, report))
    finally:
        if bridge is not None:
            logging.getLogger("flowengine").removeHandler(bridge)
        # Deliberately do NOT tear the overlay down: the HUD stays in the page
        # (with its final tree + completion banner) so it can be inspected
        # after the run. It disappears naturally when the window is closed
        # (manually, CTRL+C, or --close-after).
        adapter.disconnect()
    return results


def _resolve_scenarios(which, flows_dir):
    """Resolve the --run-tests value to a de-duplicated list of scenario ids.

    Accepts "all", plain ids, and ``tag:<tag>`` entries (expanded to every scenario carrying
    that tag), which may be mixed in a comma-separated list.
    """
    if which == "all":
        found = loader.discover_scenarios(flows_dir)
        if not found:
            log.warning("--run-tests=all but no scenarios found under %s/scenarios", flows_dir)
        return found

    items = [which] if isinstance(which, str) else list(which)
    resolved = []
    for item in items:
        if item.startswith("tag:"):
            tag = item[len("tag:"):]
            matches = loader.scenarios_with_tag(tag, flows_dir)
            if not matches:
                log.warning("no scenarios tagged %r", tag)
            resolved.extend(matches)
        else:
            resolved.append(item)

    seen, out = set(), []
    for scenario in resolved:
        if scenario not in seen:
            seen.add(scenario)
            out.append(scenario)
    return out


def _attach(profile, session_name):
    try:
        endpoint = wait_for_devtools(profile)
        from adapters.playwright_adapter import PlaywrightAdapter  # lazy: needs playwright
        return PlaywrightAdapter.connect(endpoint, DEFAULT_TIMEOUT_MS)
    except Exception as exc:
        log.error("Cannot attach to %s: %s", session_name, exc)
        return None


def wait_for_devtools(profile, timeout_s=DEVTOOLS_WAIT_S):
    """Poll ``<profile>/DevToolsActivePort`` and return http://127.0.0.1:PORT.

    Chrome writes the file as "PORT\\n/devtools/browser/<uuid>\\n" and NOT atomically,
    so a poll can land between the two writes. Requiring both lines means a partial
    read is retried instead of yielding a truncated port. The launcher deletes any
    previous file before starting Chrome (clear_devtools_port), so whatever we read
    here belongs to the window we just launched.
    """
    path = os.path.join(profile, "DevToolsActivePort")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            if len(lines) >= 2 and lines[0].strip().isdigit() and lines[1].startswith("/devtools/"):
                return "http://127.0.0.1:%s" % lines[0].strip()
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("Chrome never wrote %s (no --remote-debugging-port?)" % path)


def _run_scenario(adapter, scenario_id, session_name, flows_dir, selectors, ctx, run_dir,
                  overlay=None, report=None):
    overlay = overlay or NullOverlay()
    out_dir = artifacts.scenario_dir(run_dir, session_name, scenario_id)
    reporter = artifacts.Reporter(report or artifacts.ReportConfig(), out_dir, adapter)
    result = FlowResult(scenario=scenario_id, session=session_name, artifacts_dir=out_dir)
    started = time.time()
    try:
        steps, plan = compiler.compile_plan(scenario_id, flows_dir, selectors, ctx)
    except Exception as exc:
        result.status = ERROR
        result.error = "compile: %s" % exc
        result.duration_ms = (time.time() - started) * 1000
        reporter.finalize_compile_error(result)
        log.error("[%s] compile failed: %s", scenario_id, exc)
        return result

    overlay.flow_start(plan, role=ctx.user.get("class"))
    reporter.capture_start()
    for index, step in enumerate(steps):
        overlay.step_start(index)
        step_result = _run_step(adapter, index, step, overlay)
        result.steps.append(step_result)
        overlay.step_end(index, step_result.status, step_result.attempts, step_result.message)
        if step_result.status != PASS:
            result.status = step_result.status
            result.error = "%s(%s): %s" % (
                step.action, step.target or step.value or "", step_result.message)
            break
        reporter.capture_step()

    result.duration_ms = (time.time() - started) * 1000
    reporter.finalize(result, failed=result.status != PASS)
    passed = sum(1 for s in result.steps if s.status == PASS)
    overlay.flow_end(result.status, passed, len(steps))
    log.info("[%s] %s  (%d/%d steps, %.0f ms)", scenario_id, result.status.upper(),
             passed, len(result.steps), result.duration_ms)
    return result


# Steps that DO something to an element, as opposed to asserting about one. Only
# these get a marker: outlining every assertion target would strobe the page.
_MARKED_ACTIONS = ("click", "fill", "select", "press")


def _mark_label(step):
    """Short caption for the marker, e.g. `click` or `press Enter`."""
    if step.action == "press":
        return "press %s" % (step.value or "")
    if step.action in ("fill", "select") and step.value:
        value = str(step.value)
        return "%s %s" % (step.action, value if len(value) <= 24 else value[:21] + "...")
    return step.action


def _run_step(adapter, index, step, overlay=None):
    attempts, delay = 1, 0.0
    if step.retry:
        attempts = max(1, int(step.retry.get("attempts", 1)))
        delay = float(step.retry.get("delay", 0) or 0)
    is_assert = assertions.is_assertion(step.action)
    started = time.time()
    message, errored = "", False

    if overlay is not None and step.action in _MARKED_ACTIONS:
        # Mark BEFORE acting: a click can navigate away, and for `press` the
        # focused element is only knowable while it still has focus.
        overlay.mark(step.target if step.action != "press" else None,
                     _mark_label(step), timeout=step.timeout)

    for attempt in range(1, attempts + 1):
        try:
            if is_assert:
                ok, message = assertions.run_assertion(adapter, step)
                errored = False
            else:
                _do_action(adapter, step)
                ok, message, errored = True, "ok", False
        except Exception as exc:
            ok, message, errored = False, "%s: %s" % (type(exc).__name__, exc), True
        if ok:
            return StepResult(index, step.action, PASS, step.target, step.value,
                              message, (time.time() - started) * 1000, attempt)
        if attempt < attempts:
            if overlay is not None:
                overlay.retry(index, attempt + 1)
            if delay:
                time.sleep(delay)

    status = ERROR if errored else FAIL
    return StepResult(index, step.action, status, step.target, step.value,
                      message, (time.time() - started) * 1000, attempts)


def _do_action(adapter, step):
    action = step.action
    if action == "goto":
        adapter.goto(step.target, timeout=step.timeout)
    elif action == "fill":
        adapter.fill(step.target, step.value, timeout=step.timeout)
    elif action == "click":
        adapter.click(step.target, timeout=step.timeout)
    elif action == "select":
        adapter.select(step.target, step.value, timeout=step.timeout)
    elif action == "wait_for":
        adapter.wait_for(step.target, state=step.state or "visible", timeout=step.timeout)
    elif action == "press":
        adapter.press_key(step.value)
    else:
        raise ValueError("no handler for action %r" % action)


def _print_summary(run):
    log.info("================ RUN SUMMARY ================")
    for flow in run.flows:
        detail = ("  (%s)" % flow.error) if flow.error else ""
        log.info("  %-6s %s / %s%s", flow.status.upper(), flow.session, flow.scenario, detail)
    passed = sum(1 for flow in run.flows if flow.ok)
    log.info("  %d/%d passed", passed, len(run.flows))
    log.info("=============================================")
    events.emit("run.summary", passed=passed, total=len(run.flows),
                exit_code=run.exit_code,
                flows=[{"session": f.session, "scenario": f.scenario,
                        "status": f.status, "error": f.error,
                        "duration_ms": round(f.duration_ms), "dir": f.artifacts_dir}
                       for f in run.flows])
