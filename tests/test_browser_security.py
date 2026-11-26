"""Focused security regression tests for the optional read-only browser."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from automation.browser import BrowserAutomationError, SafeBrowser


class BrowserConfigurationTests(unittest.TestCase):
    def test_rejects_malformed_hosts_and_unsafe_limits(self):
        for host in ("*.example.com", "https://example.com", "bad host", "-bad.example"):
            with self.subTest(host=host), self.assertRaises(BrowserAutomationError):
                SafeBrowser(allowed_hosts=(host,))
        for kwargs in (
            {"timeout_ms": 999}, {"timeout_ms": True},
            {"max_page_chars": 100000}, {"max_requests": 0},
            {"max_dom_nodes": 99}, {"max_dom_nodes": 250001},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(BrowserAutomationError):
                SafeBrowser(allowed_hosts=("example.com",), **kwargs)

    def test_initial_url_rejects_non_443_and_non_allowlisted_hosts(self):
        browser = SafeBrowser(allowed_hosts=("example.com",))
        with self.assertRaises(BrowserAutomationError):
            browser._validate("https://example.com:8443/")
        with self.assertRaises(BrowserAutomationError):
            browser._validate("https://other.example/")
        with self.assertRaises(BrowserAutomationError):
            browser._validate("https://user@example.com/")

    @patch("automation.browser.socket.getaddrinfo")
    def test_initial_url_rejects_private_or_mixed_dns_answers(self, resolve):
        resolve.return_value = [
            (None, None, None, None, ("93.184.216.34", 443)),
            (None, None, None, None, ("127.0.0.1", 443)),
        ]
        browser = SafeBrowser(allowed_hosts=("example.com",))
        with self.assertRaises(BrowserAutomationError):
            browser._validate("https://example.com/")


class _Route:
    def __init__(self, url):
        self.request = types.SimpleNamespace(url=url)
        self.continued = False
        self.aborted = False

    def continue_(self):
        self.continued = True

    def abort(self):
        self.aborted = True


class _Page:
    def __init__(self, *, fail_extract=False):
        self.url = "https://example.com/"
        self.route_handler = None
        self.fail_extract = fail_extract
        self.extraction_limit = None
        self.closed = False

    def set_default_navigation_timeout(self, _timeout):
        pass

    def set_default_timeout(self, _timeout):
        pass

    def route(self, _pattern, handler):
        self.route_handler = handler

    def goto(self, url, **_kwargs):
        self.url = url
        for candidate in ("https://example.com:8443/script.js", "https://other.example/script.js"):
            route = _Route(candidate)
            self.route_handler(route)
            if not route.aborted:
                raise AssertionError("unsafe subresource request was allowed")
        safe = _Route("https://example.com/style.css")
        self.route_handler(safe)
        if not safe.continued:
            raise AssertionError("allowed HTTPS request was blocked")
        return types.SimpleNamespace(status=200)

    def title(self):
        return "Example"

    def locator(self, _selector):
        page = self

        class Locator:
            def evaluate(self, _script, budget, timeout=None):
                page.extraction_limit = budget["max_chars"]
                page.node_limit = budget["max_nodes"]
                page.extraction_timeout = timeout
                if page.fail_extract:
                    raise RuntimeError("extract failed")
                return {"text": "x" * budget["max_chars"], "visited_nodes": budget["max_nodes"]}
        return Locator()


class _Context:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def close(self):
        if getattr(self, "manager", None) is not None and not self.manager.active:
            raise AssertionError("context closed after Playwright shutdown")
        self.closed = True


class _Browser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    def new_context(self, **_kwargs):
        return self.context

    def close(self):
        if getattr(self, "manager", None) is not None and not self.manager.active:
            raise AssertionError("browser closed after Playwright shutdown")
        self.closed = True


class _PlaywrightManager:
    def __init__(self, browser):
        self.active = False
        self.browser = browser
        self.context = browser.context
        browser.manager = self
        self.context.manager = self
        self.chromium = types.SimpleNamespace(launch=lambda **_kwargs: browser)

    def __enter__(self):
        self.active = True
        return self

    def __exit__(self, *_args):
        self.active = False


class BrowserRequestAndCleanupTests(unittest.TestCase):
    def _run_with_page(self, page):
        context = _Context(page)
        browser = _Browser(context)
        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: _PlaywrightManager(browser)
        package = types.ModuleType("playwright")
        package.sync_api = module
        with patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": module}), \
             patch("automation.browser.socket.getaddrinfo", return_value=[
                 (None, None, None, None, ("93.184.216.34", 443))
             ]):
            result = SafeBrowser(
                allowed_hosts=("example.com",), max_page_chars=512
            ).read_page("https://example.com/")
        return result, context, browser, page

    def test_every_request_uses_https_allowlist_and_port_policy(self):
        result, context, browser, page = self._run_with_page(_Page())
        self.assertEqual(result["request_count"], 3)
        self.assertEqual(len(result["text"]), 512)
        self.assertEqual(page.extraction_limit, 512)
        self.assertEqual(page.node_limit, 50000)
        self.assertEqual(page.extraction_timeout, 10000)
        self.assertEqual(result["visited_dom_nodes"], 50000)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    def test_browser_resources_close_when_extraction_raises(self):
        page = _Page(fail_extract=True)
        context = _Context(page)
        browser = _Browser(context)
        module = types.ModuleType("playwright.sync_api")
        module.sync_playwright = lambda: _PlaywrightManager(browser)
        package = types.ModuleType("playwright")
        package.sync_api = module
        with patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": module}), \
             patch("automation.browser.socket.getaddrinfo", return_value=[
                 (None, None, None, None, ("93.184.216.34", 443))
             ]):
            with self.assertRaises(BrowserAutomationError):
                SafeBrowser(allowed_hosts=("example.com",)).read_page("https://example.com/")
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)


if __name__ == "__main__":
    unittest.main()
