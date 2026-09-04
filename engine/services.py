"""Asking the GUI to start, stop and watch a service, and waiting for the answer.

A scenario that needs its backend restarted cannot do it itself. The services on
the GUI's Services & Logs page are owned by *that* process - an attached one is
literally a child of it (``gui/cms_gui/services.py``) - and this engine runs in
the launcher, which is a different process. Starting our own copy would leave the
GUI watching a second Odoo it has never heard of.

So the engine asks and the GUI acts. The pipe already exists and is the one the
GUI always opens: facts go out on ``--events`` (stdout) and instructions come back
on ``--control`` (stdin), which is how ``stop-session`` reaches
:func:`engine.runner.request_session_stop`. This module is that path used the
other way round - a request out, a result back - and nothing else in the engine
knows the difference.

One request at a time per session thread, but several sessions run at once under
``--jobs``, so a request carries an ``id`` and blocks on its own
:class:`threading.Event`. :func:`deliver` is called from the launcher's control
thread and never blocks it: it fills a slot and sets an event.

When there is no GUI - a plain ``--run-tests`` in a terminal - there is nothing to
ask. :func:`request` then fails immediately and says why, rather than making every
service step wait out its whole timeout for an answer that is not coming.
"""

import logging
import threading

from engine import events

log = logging.getLogger("flowengine.services")

#: How long an imperative op (start/stop/restart) waits for the GUI to say it
#: took the job. It is not waiting for the service to be up - that is what the
#: wait actions are for - so this only has to cover the round trip.
DEFAULT_ACK_MS = 15000

#: How long a wait action waits by default. Deliberately not the engine's 30 s
#: page default: a cold Odoo with its assets to build routinely needs longer, and
#: a default that is nearly enough is worse than one that is plainly generous.
#: Any step can say ``timeout:`` and override it.
DEFAULT_WAIT_MS = 120000

#: Ops the GUI understands. The engine's action names are these; the wire uses
#: the same words so there is nothing to translate at either end.
START = "service_start"
STOP = "service_stop"
RESTART = "service_restart"
WAIT_RUNNING = "wait_for_service"
WAIT_OUT = "wait_for_out"
WAIT_CRITERION = "wait_for_criterion"

IMPERATIVE = (START, STOP, RESTART)
WAITS = (WAIT_RUNNING, WAIT_OUT, WAIT_CRITERION)

_lock = threading.Lock()
_enabled = False
_next_id = 0
#: request id -> [threading.Event, result dict or None]
_pending = {}


def configure(enabled):
    """Turn the broker on or off. Returns what it is now.

    The launcher calls this with True only when BOTH halves of the pipe are
    live - an events sink to ask through and ``--control=-`` to be answered on.
    Either one alone is a request nobody would ever reply to.
    """
    global _enabled
    with _lock:
        _enabled = bool(enabled)
        return _enabled


def enabled():
    with _lock:
        return _enabled


def reset():
    """Forget everything. For tests, and for a second run in one process."""
    global _enabled, _next_id
    with _lock:
        _enabled, _next_id, pending = False, 0, list(_pending.values())
        _pending.clear()
    for slot in pending:
        slot[0].set()


def abandon_all():
    """Release every waiting thread, because there will be no more answers.

    Called when the run is over. A session thread blocked on a reply would
    otherwise hold the launcher open for the rest of its timeout.
    """
    with _lock:
        pending = list(_pending.items())
        _pending.clear()
    for request_id, slot in pending:
        if slot[1] is None:
            slot[1] = {"ok": False, "message": "the run ended before the GUI answered"}
        slot[0].set()
        log.debug("service request %d abandoned", request_id)


def parse_ref(ref):
    """Split ``"Project/Service"`` into its two halves.

    Split on the FIRST slash: the project is the outer grouping, and a service
    named ``web/api`` is likelier than a project with a slash in its name. A ref
    with no slash is a bare service name - the GUI resolves it across every
    project and refuses it when two of them have one.
    """
    text = (ref or "").strip()
    if not text:
        return "", ""
    if "/" not in text:
        return "", text
    project, name = text.split("/", 1)
    return project.strip(), name.strip()


def request(op, ref, pattern=None, timeout_ms=None, session=None):
    """Ask the GUI for ``op`` on ``ref``; return ``(ok, message)``.

    Never raises: every failure - no GUI, a bad reference, a timeout, a service
    that went FAILED - comes back as a False with something a person can read.
    Whether that False is a FAIL or an ERROR is the caller's decision, because it
    differs by action (see docs/flows.md).
    """
    project, name = parse_ref(ref)
    if not name:
        return False, "no service named: %r" % (ref,)
    if timeout_ms is None:
        timeout_ms = DEFAULT_ACK_MS if op in IMPERATIVE else DEFAULT_WAIT_MS
    timeout = max(0.0, float(timeout_ms) / 1000.0)

    with _lock:
        if not _enabled:
            # Said once, plainly, rather than as a timeout twenty seconds later:
            # there is no GUI on this run and there never will be one.
            return False, ("service steps need the GUI: no --control channel on "
                           "this run")
        global _next_id
        _next_id += 1
        request_id = _next_id
        slot = [threading.Event(), None]
        _pending[request_id] = slot

    events.emit("service.request", id=request_id, session=session, op=op,
                project=project, service=name, pattern=pattern,
                timeout_ms=int(timeout_ms))
    log.debug("service request %d: %s %s", request_id, op, ref)

    answered = slot[0].wait(timeout)
    with _lock:
        _pending.pop(request_id, None)
    result = slot[1]

    if not answered or result is None:
        return False, "timed out after %.0fs waiting for the GUI to answer %s %s" % (
            timeout, op, ref)
    return bool(result.get("ok")), str(result.get("message") or "")


def deliver(request_id, ok=False, status="", message=""):
    """Hand one reply back to whoever is waiting for it. True when it was wanted.

    Called on the launcher's control thread. An id nobody is waiting for is
    ignored rather than fatal - a reply that arrives after its step timed out is
    late, not wrong, and taking the control thread down over it would cost every
    other session its Stop.
    """
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        return False
    with _lock:
        slot = _pending.get(request_id)
        if slot is None:
            log.debug("service result %s arrived with nobody waiting", request_id)
            return False
        slot[1] = {"ok": bool(ok), "status": status or "",
                   "message": message or status or ""}
    slot[0].set()
    return True
