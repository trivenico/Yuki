"""Optional Playwright browser reader. Navigation is HTTPS allow-list-only."""
from __future__ import annotations
from urllib.parse import urlsplit
import ipaddress
import socket


class BrowserAutomationError(RuntimeError):
    pass


class SafeBrowser:
    def __init__(self, *, allowed_hosts=(), timeout_ms=10000, headless=False):
        self.allowed_hosts = frozenset(h.lower().rstrip(".") for h in allowed_hosts)
        self.timeout_ms = timeout_ms
        self.headless = headless

    def _validate(self, url):
        if not isinstance(url, str) or len(url) > 2048:
            raise BrowserAutomationError("URL must be a bounded string.")
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise BrowserAutomationError("Only public HTTPS URLs on port 443 are accepted.")
        host = parts.hostname.lower().rstrip(".")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise BrowserAutomationError("Host is not on the explicit browser allow-list.")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise BrowserAutomationError("Non-public destinations are blocked.")
        except OSError as exc:
            raise BrowserAutomationError("Host could not be resolved.") from exc
        return url.strip()

    def read_page(self, url):
        safe_url = self._validate(url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserAutomationError("Install requirements-browser.txt to enable Playwright.") from exc
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context(accept_downloads=False, service_workers="block")
                page = context.new_page()
                page.set_default_navigation_timeout(self.timeout_ms)
                response = page.goto(safe_url, wait_until="domcontentloaded")
                final = urlsplit(page.url)
                if final.scheme != "https" or not final.hostname or final.hostname.lower().rstrip(".") not in self.allowed_hosts:
                    raise BrowserAutomationError("Navigation left the explicit allow-list; page was rejected.")
                status = response.status if response else None
                title = page.title()[:300]
                text = page.locator("body").inner_text(timeout=self.timeout_ms)[:12000]
                context.close()
                browser.close()
                return {"url": page.url, "status": status, "title": title, "text": text,
                        "untrusted_content": True, "interaction": "read-only"}
        except BrowserAutomationError:
            raise
        except Exception as exc:
            raise BrowserAutomationError(f"Browser operation failed safely ({type(exc).__name__}).") from exc
