from domain.flow import Step
from engine import assertions


class FakeAdapter:
    """In-memory stand-in for a browser: query methods only, never raise."""

    def __init__(self, visible=(), attached=(), texts=None, url="", title=""):
        self._visible = set(visible)
        self._attached = set(attached) | set(visible)
        self._texts = texts or {}
        self._url = url
        self._title = title

    def exists(self, selector, timeout=None):
        return selector in self._attached

    def visible(self, selector, timeout=None):
        return selector in self._visible

    def text(self, selector, timeout=None):
        return self._texts.get(selector, "")

    def url(self):
        return self._url

    def title(self):
        return self._title


def _ok(adapter, step):
    return assertions.run_assertion(adapter, step)[0]


def test_assert_exists():
    assert _ok(FakeAdapter(attached={".x"}), Step("assert_exists", target=".x"))
    assert not _ok(FakeAdapter(), Step("assert_exists", target=".x"))


def test_assert_visible():
    assert _ok(FakeAdapter(visible={".x"}), Step("assert_visible", target=".x"))
    assert not _ok(FakeAdapter(visible={".x"}), Step("assert_visible", target=".y"))


def test_assert_not_visible():
    adapter = FakeAdapter(visible={".x"})
    assert _ok(adapter, Step("assert_not_visible", target=".y"))
    assert not _ok(adapter, Step("assert_not_visible", target=".x"))


def test_assert_text_contains():
    adapter = FakeAdapter(texts={".m": "Hello World"})
    assert _ok(adapter, Step("assert_text_contains", target=".m", value="World"))
    assert not _ok(adapter, Step("assert_text_contains", target=".m", value="Nope"))


def test_assert_url_contains():
    adapter = FakeAdapter(url="http://localhost:8069/web#action=1")
    assert _ok(adapter, Step("assert_url_contains", value="/web"))
    assert not _ok(adapter, Step("assert_url_contains", value="/missing"))


def test_assert_title():
    assert _ok(FakeAdapter(title="My App"), Step("assert_title", value="My App"))


def test_assert_host_up_down_for_closed_port():
    # Loopback port 1 refuses instantly - a fast, offline-safe "host down" case.
    step = Step("assert_host_up", value="http://127.0.0.1:1")
    ok, _ = assertions.run_assertion(FakeAdapter(), step)
    assert not ok


def test_assert_host_up_when_reachable(monkeypatch):
    monkeypatch.setattr(assertions, "_http_reachable", lambda url, timeout=8: (True, "HTTP 200"))
    ok, _ = assertions.run_assertion(FakeAdapter(), Step("assert_host_up", value="http://x"))
    assert ok


def test_is_assertion():
    assert assertions.is_assertion("assert_visible")
    assert assertions.is_assertion("assert_host_up")
    assert not assertions.is_assertion("click")
