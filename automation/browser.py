"""Optional Playwright browser reader with bounded, allow-listed navigation.

The DNS lookup here is an early rejection check, NOT a connection-time SSRF
boundary. Deployments must additionally constrain browser egress outside
Chromium (for example with an isolated policy-enforcing proxy/network namespace).
"""
from __future__ import annotations

from urllib.parse import urlsplit
import ipaddress
import re
import socket


class BrowserAutomationError(RuntimeError):
    pass


_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalized_host(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 253:
        raise BrowserAutomationError("Allowed hosts must be bounded DNS hostnames.")
    host = value.lower().rstrip(".")
    if not host or host.startswith("*.") or any(not _HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise BrowserAutomationError("Allowed hosts must be exact, valid DNS hostnames.")
    try:
        host.encode("ascii")
    except UnicodeEncodeError as exc:
        raise BrowserAutomationError("Allowed hosts must use ASCII/IDNA form.") from exc
    return host


class SafeBrowser:
    def __init__(
        self, *, allowed_hosts=(), timeout_ms=10000, headless=False,
        max_page_chars=12000, max_requests=40,
    ):
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1000 <= timeout_ms <= 60000:
            raise BrowserAutomationError("Browser timeout must be between 1000 and 60000 ms.")
        if isinstance(max_page_chars, bool) or not isinstance(max_page_chars, int) or not 256 <= max_page_chars <= 50000:
            raise BrowserAutomationError("Page text limit must be between 256 and 50000 characters.")
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 200:
            raise BrowserAutomationError("Request limit must be between 1 and 200.")
        try:
            hosts = tuple(_normalized_host(host) for host in allowed_hosts)
        except TypeError as exc:
            raise BrowserAutomationError("Allowed hosts must be an iterable of exact hostnames.") from exc
        self.allowed_hosts = frozenset(hosts)
        self.timeout_ms = timeout_ms
        self.headless = bool(headless)
        self.max_page_chars = max_page_chars
        self.max_requests = max_requests

    def _validate(self, url):
        if not isinstance(url, str) or len(url) > 2048:
            raise BrowserAutomationError("URL must be a bounded string.")
        try:
            parts = urlsplit(url.strip())
            port = parts.port
        except ValueError as exc:
            raise BrowserAutomationError("URL is malformed.") from exc
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or port not in (None, 443):
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
        except ValueError as exc:
            raise BrowserAutomationError("Host resolution returned an invalid address.") from exc
        return url.strip()

    def read_page(self, url):
        safe_url = self._validate(url)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserAutomationError("Install requirements-browser.txt to enable Playwright.") from exc

        try:
            with sync_playwright() as playwright:
                browser = None
                try:
                    browser = playwright.chromium.launch(headless=self.headless)
                    context = None
                    try:
                        context = browser.new_context(accept_downloads=False, service_workers="block")
                        page = context.new_page()
                        page.set_default_navigation_timeout(self.timeout_ms)
                        page.set_default_timeout(self.timeout_ms)
                        request_count = 0

                        def guard_request(route):
                            nonlocal request_count
                            try:
                                target = urlsplit(route.request.url)
                                port = target.port
                                host = (target.hostname or "").lower().rstrip(".")
                                allowed = (
                                    target.scheme == "https"
                                    and port in (None, 443)
                                    and host in self.allowed_hosts
                                )
                            except ValueError:
                                allowed = False
                            request_count += 1
                            if allowed and request_count <= self.max_requests:
                                route.continue_()
                            else:
                                route.abort()

                        page.route("**/*", guard_request)
                        response = page.goto(safe_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                        final = urlsplit(page.url)
                        try:
                            final_port = final.port
                        except ValueError as exc:
                            raise BrowserAutomationError("Final navigation URL is malformed.") from exc
                        if (
                            final.scheme != "https" or not final.hostname
                            or final_port not in (None, 443)
                            or final.hostname.lower().rstrip(".") not in self.allowed_hosts
                        ):
                            raise BrowserAutomationError("Navigation left the explicit HTTPS allow-list; page was rejected.")

                        status = response.status if response else None
                        title = page.title()[:300]
                        # Walk text nodes incrementally and stop at the configured budget;
                        # do not materialize the complete body text before truncating it.
                        text = page.locator("body").evaluate(
                            """(body, limit) => {
                                const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
                                let output = "";
                                let node;
                                while ((node = walker.nextNode()) && output.length < limit) {
                                    const remaining = limit - output.length;
                                    output += (node.nodeValue || "").slice(0, remaining);
                                    if (output.length < limit) output += " ";
                                }
                                return output.slice(0, limit);
                            }""",
                            self.max_page_chars,
                        )
                        return {
                            "url": page.url, "status": status, "title": title, "text": text,
                            "untrusted_content": True, "interaction": "read-only",
                            "request_count": request_count,
                        }
                    finally:
                        if context is not None:
                            try:
                                context.close()
                            except Exception:
                                pass
                finally:
                    if browser is not None:
                        try:
                            browser.close()
                        except Exception:
                            pass
        except BrowserAutomationError:
            raise
        except Exception as exc:
            raise BrowserAutomationError(f"Browser operation failed safely ({type(exc).__name__}).") from exc
