"""Optional Playwright browser reader with firewall-authorized DNS pinning."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from core.network_firewall import NetworkFirewall


class BrowserAutomationError(RuntimeError):
    pass


class SafeBrowser:
    def __init__(self, *, allowed_hosts=(), timeout_ms=10000, headless=False,
                 resolver=None, firewall_policy=None):
        if isinstance(allowed_hosts, (str, bytes)):
            raise ValueError("browser host allow-list must be a collection")
        hosts = tuple(allowed_hosts or ())
        if any(not isinstance(host, str) or host.endswith("..") for host in hosts):
            raise ValueError("invalid browser host allow-list")
        normalized = tuple(host.lower().rstrip(".") for host in hosts)
        if len(normalized) > 100 or len(set(normalized)) != len(normalized):
            raise ValueError("invalid browser host allow-list")
        self.allowed_hosts = frozenset(normalized)
        self.firewall = firewall_policy
        if self.allowed_hosts:
            self.firewall = self.firewall or NetworkFirewall(allowed_hosts=normalized)
            if not isinstance(self.firewall, NetworkFirewall):
                raise TypeError("firewall_policy must be a NetworkFirewall")
            if not self.allowed_hosts.issubset(self.firewall.allowed_hosts):
                raise ValueError("firewall policy must allow every browser host")
            if 443 not in self.firewall.allowed_ports or "tcp" not in self.firewall.allowed_protocols:
                raise ValueError("browser firewall must allow HTTPS over TCP port 443")
        elif firewall_policy is not None:
            raise ValueError("a firewall policy cannot be used without an explicit host allow-list")
        if isinstance(timeout_ms, bool) or type(timeout_ms) is not int or not 1000 <= timeout_ms <= 120000:
            raise ValueError("timeout_ms must be between 1000 and 120000")
        self.timeout_ms = timeout_ms
        self.headless = bool(headless)
        self._resolver = resolver or socket.getaddrinfo

    def _validate(self, url):
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise BrowserAutomationError("URL must be a bounded string.")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise BrowserAutomationError("URL is invalid.") from None
        raw_host = (parts.hostname or "").lower()
        if raw_host.endswith(".."):
            raise BrowserAutomationError("URL hostname is malformed.")
        host = raw_host.rstrip(".")
        if (parts.scheme.lower() != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443) or parts.fragment):
            raise BrowserAutomationError("Only public HTTPS URLs on port 443 are accepted.")
        if host not in self.allowed_hosts:
            raise BrowserAutomationError("Host is not on the explicit browser allow-list.")
        return url, host

    def _resolve_authorized_hosts(self):
        """Resolve every permitted browser origin and pin Chromium to a vetted IP."""
        if not self.allowed_hosts or self.firewall is None:
            raise BrowserAutomationError("Browser network access is disabled until an explicit allow-list is configured.")
        rules = []
        for host in sorted(self.allowed_hosts):
            try:
                records = self._resolver(host, 443, type=socket.SOCK_STREAM)
                addresses = set()
                for record in records:
                    address = ipaddress.ip_address(record[4][0])
                    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                        address = address.ipv4_mapped
                    if not address.is_global:
                        raise BrowserAutomationError("Non-public browser destinations are blocked.")
                    addresses.add(str(address))
            except BrowserAutomationError:
                raise
            except (OSError, ValueError, IndexError, TypeError):
                raise BrowserAutomationError("Browser host could not be resolved safely.") from None
            if not addresses or len(addresses) > 32:
                raise BrowserAutomationError("Browser DNS response is invalid or too large.")
            decision = self.firewall.authorize(
                host=host, port=443, protocol="tcp", resolved_addresses=tuple(addresses),
            )
            if not decision.allowed:
                raise BrowserAutomationError("Browser destination was denied by the network firewall.")
            pinned_ip = sorted(addresses)[0]
            if ":" in pinned_ip:
                pinned_ip = f"[{pinned_ip}]"
            rules.append(f"MAP {host} {pinned_ip}")
        return ", ".join(rules)

    def read_page(self, url):
        safe_url, _ = self._validate(url)
        resolver_rules = self._resolve_authorized_hosts()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserAutomationError("Install requirements-browser.txt to enable Playwright.") from exc
        browser = context = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=self.headless,
                    args=["--no-proxy-server", f"--host-resolver-rules={resolver_rules}"],
                )
                context = browser.new_context(accept_downloads=False, service_workers="block")
                page = context.new_page()
                page.set_default_navigation_timeout(self.timeout_ms)

                def guard_request(route):
                    try:
                        target = urlsplit(route.request.url)
                        host = (target.hostname or "").lower().rstrip(".")
                        permitted = (
                            target.scheme.lower() == "https"
                            and target.port in (None, 443)
                            and target.username is None
                            and target.password is None
                            and host in self.allowed_hosts
                        )
                    except ValueError:
                        permitted = False
                    if permitted:
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", guard_request)
                response = page.goto(safe_url, wait_until="domcontentloaded")
                final = urlsplit(page.url)
                final_host = (final.hostname or "").lower().rstrip(".")
                if (final.scheme.lower() != "https" or final_host not in self.allowed_hosts
                        or final.port not in (None, 443)):
                    raise BrowserAutomationError("Navigation left the explicit allow-list; page was rejected.")
                status = response.status if response else None
                title = page.title()[:300]
                text = page.locator("body").inner_text(timeout=self.timeout_ms)[:12000]
                return {"url": page.url, "status": status, "title": title, "text": text,
                        "untrusted_content": True, "interaction": "read-only"}
        except BrowserAutomationError:
            raise
        except Exception as exc:
            raise BrowserAutomationError(f"Browser operation failed safely ({type(exc).__name__}).") from exc
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
