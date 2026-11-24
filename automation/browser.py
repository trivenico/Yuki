"""Optional Playwright browser reader. Navigation is HTTPS allow-list-only."""
from __future__ import annotations

from urllib.parse import urlsplit
import ipaddress
import socket


class BrowserAutomationError(RuntimeError):
    pass


class SafeBrowser:
    def __init__(self, *, allowed_hosts=(), timeout_ms=10000, headless=False):
        if not isinstance(allowed_hosts, (set, frozenset, tuple, list)):
            raise ValueError("allowed_hosts must be a collection")
        self.allowed_hosts = frozenset(
            host.lower().rstrip(".") for host in allowed_hosts
            if isinstance(host, str) and host and host == host.strip()
        )
        if len(self.allowed_hosts) != len(allowed_hosts):
            raise ValueError("allowed_hosts contains an invalid hostname")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 100 <= timeout_ms <= 120000:
            raise ValueError("timeout_ms must be between 100 and 120000")
        self.timeout_ms = timeout_ms
        self.headless = bool(headless)

    def _parse_allowed_url(self, url):
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise BrowserAutomationError("URL must be a bounded string.")
        if any(ord(char) < 0x21 or ord(char) == 0x7f for char in url):
            raise BrowserAutomationError("URL contains invalid characters.")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise BrowserAutomationError("URL is invalid.") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme.lower() != "https" or not host
                or parts.username is not None or parts.password is not None
                or port not in (None, 443) or parts.fragment):
            raise BrowserAutomationError("Only public HTTPS URLs on port 443 are accepted.")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise BrowserAutomationError("Host is not on the explicit browser allow-list.")
        return host

    def _validate(self, url):
        host = self._parse_allowed_url(url)
        try:
            addresses = {
                item[4][0] for item in socket.getaddrinfo(
                    host, 443, type=socket.SOCK_STREAM
                )
            }
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise BrowserAutomationError("Non-public destinations are blocked.")
        except (OSError, ValueError, IndexError, TypeError):
            raise BrowserAutomationError("Host could not be resolved to public addresses.") from None
        return url.strip()

    def read_page(self, url):
        safe_url = self._validate(url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserAutomationError("Install requirements-browser.txt to enable Playwright.") from exc
        browser = context = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    accept_downloads=False, service_workers="block",
                    java_script_enabled=True,
                )
                page = context.new_page()
                page.set_default_navigation_timeout(self.timeout_ms)

                def guard_request(route):
                    try:
                        # Apply the full URL policy to every navigation, redirect,
                        # iframe, and subresource, not only the initial page URL.
                        self._parse_allowed_url(route.request.url)
                    except BrowserAutomationError:
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", guard_request)
                response = page.goto(safe_url, wait_until="domcontentloaded")
                final = urlsplit(page.url)
                try:
                    self._parse_allowed_url(page.url)
                except BrowserAutomationError:
                    raise BrowserAutomationError(
                        "Navigation left the explicit HTTPS allow-list; page was rejected."
                    ) from None
                status = response.status if response else None
                title = page.title()[:300]
                text = page.locator("body").inner_text(timeout=self.timeout_ms)[:12000]
                final_url = page.url
                return {
                    "url": final_url, "status": status, "title": title,
                    "text": text, "untrusted_content": True,
                    "interaction": "read-only",
                }
        except BrowserAutomationError:
            raise
        except Exception as exc:
            raise BrowserAutomationError(
                f"Browser operation failed safely ({type(exc).__name__})."
            ) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
