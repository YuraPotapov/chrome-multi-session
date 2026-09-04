import json
import threading

import pytest

from domain.flow import Step
from engine import assertions, events, services


class Sink:
    """Stands in for the --events stream, keeping what was written to it."""

    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        pass

    def events(self):
        return [json.loads(line) for line in self.lines if line.strip()]


@pytest.fixture
def sink(monkeypatch):
    """A broker that is on, emitting into a sink, and reset again afterwards."""
    stream = Sink()
    monkeypatch.setattr(events, "_stream", stream)
    services.reset()
    services.configure(True)
    yield stream
    services.reset()


def _answer(sink, **fields):
    """Reply to the one request in the sink, the way the control thread does."""
    request = sink.events()[-1]
    services.deliver(request["id"], **fields)
    return request


def _in_a_moment(fn):
    timer = threading.Timer(0.05, fn)
    timer.daemon = True
    timer.start()
    return timer


def test_a_request_is_emitted_and_its_answer_comes_back(sink):
    result = {}

    def ask():
        result["r"] = services.request(services.WAIT_OUT, "Storefront/Web",
                                       pattern=".+:8069", timeout_ms=5000,
                                       session="admin")

    thread = threading.Thread(target=ask)
    thread.start()
    # Give the request time to reach the sink, then answer it.
    for _ in range(100):
        if sink.events():
            break
        threading.Event().wait(0.01)
    request = _answer(sink, ok=True, status="running", message="matched")
    thread.join(5)

    assert request["kind"] == "service.request"
    assert request["op"] == "wait_for_out"
    assert (request["project"], request["service"]) == ("Storefront", "Web")
    assert request["pattern"] == ".+:8069"
    assert request["session"] == "admin"
    assert result["r"] == (True, "matched")


def test_a_refusal_comes_back_as_its_message(sink):
    _in_a_moment(lambda: _answer(sink, ok=False, status="failed",
                                 message="no service Storefront/Nope"))
    ok, message = services.request(services.START, "Storefront/Nope", timeout_ms=3000)
    assert not ok
    assert "no service" in message


def test_a_wait_with_no_answer_times_out(sink):
    ok, message = services.request(services.WAIT_RUNNING, "Storefront/Web", timeout_ms=60)
    assert not ok
    assert "timed out" in message
    # And it left nothing behind for the next request to trip over.
    assert not services._pending


def test_without_a_control_channel_it_fails_at_once_rather_than_waiting():
    services.reset()          # configure() was never called: no GUI
    ok, message = services.request(services.START, "Storefront/Web", timeout_ms=60000)
    assert not ok
    assert "need the GUI" in message


def test_an_answer_nobody_is_waiting_for_is_ignored(sink):
    # A reply that arrives after its step gave up is late, not fatal.
    assert services.deliver(4321, ok=True) is False
    assert services.deliver("not a number", ok=True) is False


def test_abandon_all_releases_a_waiting_thread(sink):
    result = {}

    def ask():
        result["r"] = services.request(services.WAIT_RUNNING, "Storefront/Web",
                                       timeout_ms=60000)

    thread = threading.Thread(target=ask)
    thread.start()
    for _ in range(100):
        if sink.events():
            break
        threading.Event().wait(0.01)
    services.abandon_all()
    thread.join(5)

    assert not thread.is_alive()          # it did not wait out its whole minute
    assert result["r"][0] is False


def test_ids_are_unique_across_threads(sink):
    threads = [threading.Thread(target=services.request,
                               args=(services.WAIT_RUNNING, "Storefront/Web"),
                               kwargs={"timeout_ms": 80})
               for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    ids = [event["id"] for event in sink.events()]
    assert len(ids) == 8
    assert len(set(ids)) == 8


def test_parse_ref_splits_on_the_first_slash():
    assert services.parse_ref("Storefront/Web") == ("Storefront", "Web")
    assert services.parse_ref("Storefront/web/api") == ("Storefront", "web/api")
    assert services.parse_ref(" Storefront / Web ") == ("Storefront", "Web")
    # No slash: a bare service name, for the GUI to resolve across projects.
    assert services.parse_ref("Web") == ("", "Web")
    assert services.parse_ref("") == ("", "")


def test_a_reference_with_no_service_never_reaches_the_wire(sink):
    ok, message = services.request(services.START, "  ", timeout_ms=60000)
    assert not ok
    assert not sink.events()


# -- the assertion half ------------------------------------------------------

def test_the_wait_assertions_are_registered():
    for name in ("wait_for_service", "wait_for_out", "wait_for_criterion"):
        assert assertions.is_assertion(name)


def test_a_wait_that_fails_is_a_clean_false_naming_what_it_wanted(sink):
    # No GUI to answer, so this is the fail-fast path - the point being that an
    # assertion returns (ok, message) rather than raising.
    services.reset()
    ok, message = assertions.run_assertion(
        None, Step("wait_for_out", target="Storefront/Web", value=".+:8069"))
    assert not ok
    assert "Storefront/Web" in message and ".+:8069" in message


def test_a_wait_passes_on_a_true_answer(sink):
    _in_a_moment(lambda: _answer(sink, ok=True, status="running", message="matched"))
    ok, message = assertions.run_assertion(
        None, Step("wait_for_service", target="Storefront/Web", timeout=3000))
    assert ok
    assert "Storefront/Web" in message


def test_a_wait_passes_its_step_timeout_through(sink):
    _in_a_moment(lambda: _answer(sink, ok=True, status="running"))
    assertions.run_assertion(
        None, Step("wait_for_criterion", target="Storefront/Web", value="start",
                   timeout=4500))
    assert sink.events()[-1]["timeout_ms"] == 4500


def test_a_wait_with_no_timeout_gets_the_generous_default(sink):
    _in_a_moment(lambda: _answer(sink, ok=True, status="running"))
    assertions.run_assertion(None, Step("wait_for_service", target="Storefront/Web"))
    assert sink.events()[-1]["timeout_ms"] == services.DEFAULT_WAIT_MS
