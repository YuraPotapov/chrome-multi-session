"""Assertion registry.

Assertions are kept separate from actions: each takes the adapter and a
normalized Step and returns ``(ok, message)``. Query methods on the adapter never
raise on absence, so a failed assertion is a clean False rather than an exception.

To add one: write the function, add it to :data:`ASSERTIONS`, AND add its name to
the matching set in :mod:`engine.compiler` (``SELECTOR_ONLY`` /
``SELECTOR_AND_VALUE`` / ``VALUE_ONLY``). Both halves are required - without the
second, ``_validate`` rejects it as an unknown action. See docs/flows.md.

Not everything here asks the browser. ``assert_host_up`` asks the network and the
three ``wait_for_*`` service steps ask the GUI (:mod:`engine.services`); what puts
them in this module is the shape of the answer, not where it came from.
"""

import ssl
import urllib.error
import urllib.request

from engine import services


def _assert_exists(adapter, step):
    ok = adapter.exists(step.target, timeout=step.timeout)
    return ok, ("exists: %s" % step.target) if ok else ("not found: %s" % step.target)


def _assert_visible(adapter, step):
    ok = adapter.visible(step.target, timeout=step.timeout)
    return ok, ("visible: %s" % step.target) if ok else ("not visible: %s" % step.target)


def _assert_not_visible(adapter, step):
    ok = not adapter.visible(step.target, timeout=step.timeout)
    return ok, ("hidden: %s" % step.target) if ok else ("unexpectedly visible: %s" % step.target)


def _assert_text_contains(adapter, step):
    text = adapter.text(step.target, timeout=step.timeout) or ""
    ok = step.value in text
    return ok, "expected %r in %s; got %r" % (step.value, step.target, text[:200])


def _assert_url_contains(adapter, step):
    url = adapter.url()
    return (step.value in url), "expected %r in url; got %s" % (step.value, url)


def _assert_title(adapter, step):
    title = adapter.title()
    return (step.value in title), "expected %r in title; got %r" % (step.value, title)


# Lenient TLS: a reachability check only cares that the host ANSWERS, not that its
# certificate validates - so a self-signed/expired cert still counts as "up".
_INSECURE_TLS = ssl.create_default_context()
_INSECURE_TLS.check_hostname = False
_INSECURE_TLS.verify_mode = ssl.CERT_NONE


def _http_reachable(url, timeout=8):
    """Return (ok, detail): ok is True if the server answers with ANY HTTP status."""
    try:
        resp = urllib.request.urlopen(url, timeout=timeout, context=_INSECURE_TLS)
        return True, "HTTP %s" % resp.getcode()
    except urllib.error.HTTPError as exc:
        return True, "HTTP %s" % exc.code            # server answered (even 4xx/5xx)
    except Exception as exc:                          # refused / DNS / timeout => down
        return False, "%s: %s" % (type(exc).__name__, exc)


def _assert_host_up(adapter, step):
    # Browser-independent: the very first test is simply "did the host answer?", so a
    # down server fails in ~1s with a clear message instead of a selector timeout
    # deeper in the flow. ``step.value`` is the URL/origin to probe.
    ok, detail = _http_reachable(step.value)
    return ok, ("host answered (%s)" % detail) if ok else ("host not answering (%s)" % detail)


# The three waits on a service. They are here rather than among the actions for
# the reason at the top of this module: each one asks a question and answers it
# with (ok, message), so a service that never comes up is a FAIL with a readable
# reason rather than an exception. Like _assert_host_up they never touch the
# adapter - the answer comes from the GUI, over the control channel.
def _wait_for_service(adapter, step):
    ok, detail = services.request(services.WAIT_RUNNING, step.target,
                                  timeout_ms=step.timeout)
    return ok, ("%s is running" % step.target) if ok else (
        "%s did not come up (%s)" % (step.target, detail))


def _wait_for_out(adapter, step):
    ok, detail = services.request(services.WAIT_OUT, step.target,
                                  pattern=step.value, timeout_ms=step.timeout)
    return ok, ("%s printed %r" % (step.target, step.value)) if ok else (
        "%s never printed %r (%s)" % (step.target, step.value, detail))


def _wait_for_criterion(adapter, step):
    ok, detail = services.request(services.WAIT_CRITERION, step.target,
                                  pattern=step.value, timeout_ms=step.timeout)
    return ok, ("%s: %r is lit" % (step.target, step.value)) if ok else (
        "%s: %r never lit (%s)" % (step.target, step.value, detail))


ASSERTIONS = {
    "assert_exists": _assert_exists,
    "assert_visible": _assert_visible,
    "assert_not_visible": _assert_not_visible,
    "assert_text_contains": _assert_text_contains,
    "assert_url_contains": _assert_url_contains,
    "assert_title": _assert_title,
    "assert_host_up": _assert_host_up,
    "wait_for_service": _wait_for_service,
    "wait_for_out": _wait_for_out,
    "wait_for_criterion": _wait_for_criterion,
}


def is_assertion(action):
    return action in ASSERTIONS


def run_assertion(adapter, step):
    return ASSERTIONS[step.action](adapter, step)
