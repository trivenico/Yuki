"""DNS-pinned bounded HTTPS text fetch for explicitly trusted public hosts."""
from __future__ import annotations

import html
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit


class WebSkillError(RuntimeError):
    pass


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, *, timeout, context):
        super().__init__(host, port=443, timeout=timeout, context=context)
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class WebSkill:
    name = "web_fetch"
    description = "Fetch a small public HTTPS page and extract readable text."
    risk = "medium"
    requires_approval = True

    def __init__(self, *, allowed_hosts=(), timeout=8, max_bytes=500000,
                 resolver=None, connection_factory=None):
        if not isinstance(allowed_hosts, (set, frozenset, tuple, list)):
            raise ValueError("allowed_hosts must be a collection")
        self.allowed_hosts = frozenset(
            host.lower().rstrip(".") for host in allowed_hosts
            if isinstance(host, str) and host and host == host.strip()
        )
        if len(self.allowed_hosts) != len(allowed_hosts):
            raise ValueError("allowed_hosts contains an invalid hostname")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 2_000_000:
            raise ValueError("max_bytes must be between 1 and 2000000")
        self.timeout, self.max_bytes = float(timeout), max_bytes
        self._resolver = resolver or socket.getaddrinfo
        self._connection_factory = connection_factory or _PinnedHTTPSConnection
        self._ssl_context = ssl.create_default_context()

    def _parse_url(self, url):
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise WebSkillError("URL must be a bounded string.")
        if any(ord(char) < 0x21 or ord(char) == 0x7f for char in url):
            raise WebSkillError("URL contains invalid characters.")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise WebSkillError("URL is invalid.") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme.lower() != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443)
                or parts.fragment):
            raise WebSkillError("Only public HTTPS URLs on the default port are accepted.")
        if (host in {"localhost", "localhost.localdomain"}
                or host.endswith((".local", ".internal"))
                or host not in self.allowed_hosts):
            raise WebSkillError("Host is not on the configured public HTTPS allow-list.")
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
        return host, target

    def _resolve_public_ip(self, host):
        try:
            records = self._resolver(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise WebSkillError("Host could not be resolved.") from None
        addresses = set()
        for record in records:
            try:
                address = ipaddress.ip_address(record[4][0])
            except (ValueError, IndexError, TypeError):
                raise WebSkillError("Host resolution returned an invalid address.") from None
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global:
                raise WebSkillError("URL resolves to a non-public address.")
            addresses.add(str(address))
        if not addresses:
            raise WebSkillError("Host could not be resolved.")
        # Connect to this exact validated address; do not resolve the hostname again.
        return sorted(addresses)[0]

    def execute(self, url):
        host, target = self._parse_url(url)
        address = self._resolve_public_ip(host)
        connection = self._connection_factory(
            host, address, timeout=self.timeout, context=self._ssl_context
        )
        try:
            connection.request(
                "GET", target,
                headers={"User-Agent": "Yuki/0.1", "Accept": "text/html,text/plain"},
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise WebSkillError("Redirects are not followed.")
            if response.status < 200 or response.status >= 300:
                raise WebSkillError("Page fetch failed.")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "text/plain"}:
                raise WebSkillError("Only HTML or plain-text pages are accepted.")
            body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise WebSkillError("Page exceeds the configured size limit.")
            decoded = body.decode("utf-8", errors="replace")
            decoded = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\\1>", " ", decoded)
            decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
            text = re.sub(r"\\s+", " ", html.unescape(decoded)).strip()
            return {"url": url, "title": text[:160], "text": text[:12000]}
        except WebSkillError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
            raise WebSkillError("Page fetch failed or timed out.") from None
        finally:
            connection.close()
