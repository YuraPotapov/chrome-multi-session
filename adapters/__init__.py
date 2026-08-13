"""Browser adapters.

The engine talks to the abstract :class:`~adapters.base.BrowserAdapter`, never
to Playwright/Selenium directly, so a different backend can be dropped in later.
Only :mod:`adapters.playwright_adapter` imports Playwright, and only lazily, so
the domain/engine logic stays importable without it.
"""
