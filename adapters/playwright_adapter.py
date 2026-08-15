"""Playwright implementation of :class:`~adapters.base.BrowserAdapter`.

It attaches to the Chrome the launcher already started (and the auto-login
extension already signed in) via ``connect_over_cdp`` - so it drives the real,
logged-in window and needs no Playwright-bundled browser download. Playwright is
imported lazily inside :meth:`connect`, keeping this module importable (for unit
tests) without the package installed.
"""

import logging

from adapters.base import BrowserAdapter

log = logging.getLogger("flowengine.adapter")

# Playwright expresses "not found within timeout" as its own TimeoutError. We
# translate that into a clean False/"" for query methods instead of an
# exception, so assertions report pass/fail rather than crashing the run.
DEFAULT_TIMEOUT_MS = 30000


class PlaywrightAdapter(BrowserAdapter):
    def __init__(self, playwright, browser, page):
        self._pw = playwright
        self._browser = browser
        self._page = page
        self._console = []
        # Import here so the symbol is available for except-clauses without a
        # module-level Playwright import.
        from playwright.sync_api import TimeoutError as _Timeout
        self._Timeout = _Timeout
        page.on("console", self._on_console)

    # ------------------------------------------------------------------ setup
    @classmethod
    def connect(cls, endpoint, default_timeout_ms=DEFAULT_TIMEOUT_MS):
        """Attach to a running Chrome at ``endpoint`` (http://127.0.0.1:PORT).

        Grabs the first existing page across the browser's contexts - that is
        the launcher's already-open, already-logged-in app window.
        """
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(endpoint)
        except Exception:
            pw.stop()
            raise
        page = cls._pick_page(browser)
        page.set_default_timeout(default_timeout_ms)
        log.debug("Attached over CDP at %s; driving page %s", endpoint, page.url)
        return cls(pw, browser, page)

    @staticmethod
    def _pick_page(browser):
        for context in browser.contexts:
            if context.pages:
                return context.pages[0]
        # No page open yet (unusual) - create one in the default context.
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return context.new_page()

    def _on_console(self, msg):
        try:
            self._console.append("%s: %s" % (msg.type, msg.text))
        except Exception:  # never let logging break a run
            pass

    # ---------------------------------------------------------------- actions
    def goto(self, url, timeout=None):
        self._page.goto(url, timeout=timeout)

    def fill(self, selector, value, timeout=None):
        self._page.fill(selector, value, timeout=timeout)

    def click(self, selector, timeout=None):
        self._page.click(selector, timeout=timeout)

    def select(self, selector, value, timeout=None):
        # By option VALUE, not label: values are field names (company_id), which are
        # identical in every UI language, while labels are translated.
        self._page.select_option(selector, value=value, timeout=timeout)

    def wait_for(self, selector, state="visible", timeout=None):
        self._page.wait_for_selector(selector, state=state, timeout=timeout)

    def press_key(self, key):
        # Press on whatever is currently focused (e.g. right after a click:).
        self._page.keyboard.press(key)

    # ---------------------------------------------------------------- queries
    def exists(self, selector, timeout=None):
        return self._present(selector, "attached", timeout)

    def visible(self, selector, timeout=None):
        return self._present(selector, "visible", timeout)

    def _present(self, selector, state, timeout):
        try:
            self._page.wait_for_selector(selector, state=state, timeout=timeout)
            return True
        except self._Timeout:
            return False

    def text(self, selector, timeout=None):
        try:
            value = self._page.text_content(selector, timeout=timeout)
        except self._Timeout:
            return ""
        return value or ""

    def url(self):
        return self._page.url

    def title(self):
        return self._page.title()

    # -------------------------------------------------------------- overlay
    # The HUD lives inside the page as an isolated, pointer-events:none Shadow
    # DOM (see engine/hud.js). Rendering must never break a run, so every call
    # swallows exceptions - a page that navigated mid-render, or a transient
    # evaluate error, just drops that frame.
    def overlay_setup(self, js_source):
        try:
            # add_init_script re-installs the renderer on every future document
            # (reloads / goto); evaluate installs it into the current one.
            self._page.add_init_script(js_source)
            self._page.evaluate(js_source)
        except Exception as exc:
            log.debug("overlay_setup failed: %s", exc)

    def overlay_render(self, state):
        try:
            self._page.evaluate("s => window.__ExecHud && window.__ExecHud.render(s)", state)
        except Exception as exc:
            log.debug("overlay_render failed: %s", exc)

    def overlay_mark(self, selector, label=None, timeout=None):
        """Flash the HUD's marker over the element a step is about to act on.

        ``selector`` None means "whatever currently has focus" - that is what a
        bare `press` acts on, and the focused element is otherwise invisible in a
        screenshot.

        Resolution goes through Playwright rather than querySelector because these
        selectors use Playwright's own syntax (``:nth-match``, ``:text-is``), which
        the browser cannot parse. Best-effort throughout: a marker that cannot be
        drawn must never fail the step it is decorating.
        """
        try:
            if selector is None:
                self._page.evaluate(
                    "label => window.__ExecHud && window.__ExecHud.mark(document.activeElement,"
                    " label)", label)
            else:
                self._page.locator(selector).first.evaluate(
                    "(el, label) => window.__ExecHud && window.__ExecHud.mark(el, label)",
                    label, timeout=timeout)
        except Exception as exc:
            log.debug("overlay_mark failed for %r: %s", selector, exc)

    def overlay_teardown(self):
        try:
            self._page.evaluate("() => window.__ExecHud && window.__ExecHud.remove()")
        except Exception as exc:
            log.debug("overlay_teardown failed: %s", exc)

    # -------------------------------------------------------------- recorder
    def recorder_setup(self, js_source):
        """Same two-step install as the overlay: this document, and every next one."""
        try:
            self._page.add_init_script(js_source)
            self._page.evaluate(js_source)
        except Exception as exc:
            log.debug("recorder_setup failed: %s", exc)

    def recorder_call(self, expression, argument=None):
        """Call into window.__Recorder, tolerating a page that has none yet.

        This is polled several times a second while a recording is idle, and the
        page can be navigating at any moment - so a failure here is normal and
        means "nothing to report", never an error worth stopping for.
        """
        try:
            return self._page.evaluate(
                # The name has to be `recorder`: that is what the callers' little
                # expressions are written against.
                "arg => { const recorder = window.__Recorder;"
                " return recorder ? (%s) : null; }" % expression, argument)
        except Exception as exc:
            log.debug("recorder_call failed: %s", exc)
            return None

    # ------------------------------------------------------------ diagnostics
    def screenshot(self, path):
        try:
            self._page.screenshot(path=path, full_page=True)
        except Exception:
            # full_page can fail on some layouts; fall back to the viewport.
            self._page.screenshot(path=path)

    def content(self):
        return self._page.content()

    def console_logs(self):
        return list(self._console)

    def disconnect(self):
        # Only detach Playwright; do NOT close the browser. The launcher owns
        # the Chrome process and closes it gracefully (close_all) so the login
        # session is flushed to disk. Closing it here would look like a crash.
        try:
            self._pw.stop()
        except Exception:
            pass
