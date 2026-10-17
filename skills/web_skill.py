"""Bounded HTTPS text fetch. Rejects local/private destinations and redirects."""
import html
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class WebSkillError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebSkill:
    name = "web_fetch"
    description = "Fetch a small public HTTPS page and extract readable text."
    risk = "medium"
    requires_approval = True

    def __init__(self, *, allowed_hosts=(), timeout=8, max_bytes=500000):
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.timeout, self.max_bytes = timeout, max_bytes

    def execute(self, url):
        if not isinstance(url, str) or len(url) > 2048:
            raise WebSkillError("URL must be a bounded string.")
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            raise WebSkillError("Only public HTTPS URLs on the default port are accepted.")
        host = parts.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            raise WebSkillError("Local hosts are blocked.")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise WebSkillError("Configure an explicit web_allowed_hosts allow-list before fetching pages.")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise WebSkillError("URL resolves to a non-public address.")
        except OSError as exc:
            raise WebSkillError("Host could not be resolved.") from exc
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise WebSkillError("Host is not on the configured allow-list.")
        request = urllib.request.Request(url, headers={"User-Agent": "Yuki/0.1", "Accept": "text/html,text/plain"})
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=self.timeout) as response:
                if not any(kind in response.headers.get("Content-Type", "").lower() for kind in ("text/html", "text/plain")):
                    raise WebSkillError("Only HTML or plain-text pages are accepted.")
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise WebSkillError("Page exceeds the configured size limit.")
                decoded = body.decode("utf-8", errors="replace")
                decoded = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\\1>", " ", decoded)
                decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
                text = re.sub(r"\\s+", " ", html.unescape(decoded)).strip()
                return {"url": url, "title": text[:160], "text": text[:12000]}
        except urllib.error.HTTPError as exc:
            raise WebSkillError(f"HTTP request failed with status {exc.code}; redirects are not followed.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise WebSkillError("Page fetch failed or timed out.") from exc
