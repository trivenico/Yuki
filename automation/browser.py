"""Optional Playwright browser reader. Navigation is HTTPS allow-list-only."""
from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urlsplit

from core.authorization import AuthorizationRequest, Principal
from core.authorization_gateway import AuthorizationGateway


class BrowserAutomationError(RuntimeError):
    pass


class SafeBrowser:
    def __init__(
        self, *, allowed_hosts=(), timeout_ms=10000, headless=False,
        authorization_gateway: AuthorizationGateway | None = None,
        principal: Principal | None = None, policy_revision: str | None = None,
        task_id: str | None = None, delegation_id: str | None = None,
        approval_id: str | None = None,
    ):
        self.allowed_hosts = frozenset(h.lower().rstrip(".") for h in allowed_hosts)
        self.timeout_ms = timeout_ms
        self.headless = headless
        if authorization_gateway is not None and not isinstance(authorization_gateway, AuthorizationGateway):
            raise TypeError("authorization_gateway must be an AuthorizationGateway")
        if (authorization_gateway is None) != (principal is None):
            raise ValueError("browser gateway and authenticated principal must be configured together")
        if principal is not None and not isinstance(principal, Principal):
            raise TypeError("browser navigation requires an authenticated Principal")
        self._authorization_gateway = authorization_gateway
        self._principal = principal
        self._policy_revision = policy_revision
        self._task_id = task_id
        self._delegation_id = delegation_id
        self._approval_id = approval_id
        if authorization_gateway is not None:
            # Validate immutable authorization context at construction.
            AuthorizationRequest(
                principal=principal, action="browser:navigate", resource="browser:configuration",
                risk="low", task_id=task_id, delegation_id=delegation_id,
                approval_id=approval_id, expected_policy_revision=policy_revision,
            )

    def _validate(self, url):
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise BrowserAutomationError("URL must be a bounded string.")
        try:
            parts = urlsplit(url.strip())
            port = parts.port
        except ValueError:
            raise BrowserAutomationError("URL is invalid.") from None
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or port not in (None, 443) or parts.fragment):
            raise BrowserAutomationError("Only public HTTPS URLs on port 443 are accepted.")
        try:
            url.strip().encode("utf-8")
        except UnicodeEncodeError:
            raise BrowserAutomationError("URL must be valid UTF-8 text.") from None
        host = parts.hostname.lower().rstrip(".")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise BrowserAutomationError("Host is not on the explicit browser allow-list.")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise BrowserAutomationError("Non-public destinations are blocked.")
        except (OSError, ValueError):
            raise BrowserAutomationError("Host could not be resolved safely.") from None
        return url.strip()

    def _authorize_navigation(self, url):
        """Authorize the exact network destination immediately before navigation/request."""
        safe_url = self._validate(url)
        if self._authorization_gateway is not None:
            # Hash the complete normalized destination: query parameters can
            # change the remote operation, but may also contain credentials.
            # A digest binds policy to the exact URL without logging secrets.
            digest = hashlib.sha256(safe_url.encode("utf-8")).hexdigest()
            request = AuthorizationRequest(
                principal=self._principal,
                action="browser:navigate",
                resource="browser:sha256:" + digest,
                risk="low",
                task_id=self._task_id,
                delegation_id=self._delegation_id,
                approval_id=self._approval_id,
                expected_policy_revision=self._policy_revision,
            )
            try:
                decision = self._authorization_gateway.authorize(request)
            except Exception:
                decision = None
            if decision is None or getattr(decision, "allowed", None) is not True:
                raise BrowserAutomationError("Navigation denied by Yuki's central security policy.")
        return safe_url

    def read_page(self, url):
        safe_url = self._authorize_navigation(url)
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

                def guard_request(route):
                    try:
                        # Re-authorize every request, including redirects, scripts,
                        # frames and subresources. A previously approved origin is
                        # not a transferable grant for a different URL.
                        self._authorize_navigation(route.request.url)
                    except BrowserAutomationError:
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", guard_request)
                response = page.goto(safe_url, wait_until="domcontentloaded")
                final_url = self._authorize_navigation(page.url)
                status = response.status if response else None
                title = page.title()[:300]
                text = page.locator("body").inner_text(timeout=self.timeout_ms)[:12000]
                context.close()
                browser.close()
                return {
                    "url": final_url, "status": status, "title": title, "text": text,
                    "untrusted_content": True, "interaction": "read-only",
                }
        except BrowserAutomationError:
            raise
        except Exception as exc:
            raise BrowserAutomationError(f"Browser operation failed safely ({type(exc).__name__}).") from exc
