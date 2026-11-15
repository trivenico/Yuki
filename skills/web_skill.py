"""Bounded, firewall-enforced HTTPS text fetch for explicitly trusted hosts."""
import html
import re
import urllib.request
from urllib.parse import urlsplit

from core.pinned_https_provider_transport import PinnedHTTPSProviderTransport, PinnedHTTPTransportError


_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$")


class WebSkillError(RuntimeError):
    pass


class WebSkill:
    name = "web_fetch"
    description = "Fetch a small public HTTPS page and extract readable text."
    risk = "medium"
    requires_approval = True

    def __init__(self, *, allowed_hosts=(), timeout=8, max_bytes=500000, firewall_policy=None):
        if isinstance(allowed_hosts, (str, bytes)) or not isinstance(allowed_hosts, (tuple, list, set, frozenset)):
            raise ValueError("allowed_hosts must be a collection of exact hostnames.")
        normalized_hosts = set()
        for host in allowed_hosts:
            if not isinstance(host, str) or host != host.strip() or host.endswith(".."):
                raise ValueError("invalid web host allow-list entry.")
            normalized = host.lower()
            if normalized.endswith("."):
                normalized = normalized[:-1]
            if not _HOSTNAME.fullmatch(normalized):
                raise ValueError("invalid web host allow-list entry.")
            if normalized in normalized_hosts:
                raise ValueError("duplicate web host allow-list entry.")
            normalized_hosts.add(normalized)
        self.allowed_hosts = frozenset(normalized_hosts)
        self.timeout, self.max_bytes = timeout, max_bytes
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2_000_000:
            raise ValueError("max_bytes must be between 1 and 2000000")
        # Build the pinned transport internally; callers cannot substitute an
        # untrusted transport that bypasses DNS pinning or firewall policy.
        self._transport = None
        if self.allowed_hosts:
            self._transport = PinnedHTTPSProviderTransport(
                allowed_hosts=self.allowed_hosts, timeout=timeout, firewall_policy=firewall_policy,
            )

    def execute(self, url):
        if not isinstance(url, str) or not 1 <= len(url) <= 2048:
            raise WebSkillError("URL must be a bounded string.")
        if url != url.strip() or any(ord(char) < 0x20 or ord(char) == 0x7f for char in url):
            raise WebSkillError("URL contains surrounding whitespace or control characters.")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise WebSkillError("URL is invalid.") from None
        raw_host = (parts.hostname or "").lower()
        if raw_host.endswith(".."):
            raise WebSkillError("URL hostname is malformed.")
        host = raw_host[:-1] if raw_host.endswith(".") else raw_host
        if (parts.scheme.lower() != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443) or parts.fragment):
            raise WebSkillError("Only public HTTPS URLs on the default port are accepted.")
        if not self.allowed_hosts or host not in self.allowed_hosts:
            raise WebSkillError("Configure an explicit web_allowed_hosts allow-list before fetching pages.")
        if self._transport is None:
            raise WebSkillError("Firewall-enforced web transport is unavailable.")
        request = urllib.request.Request(
            url, headers={"User-Agent": "Yuki/0.1", "Accept": "text/html,text/plain"},
        )
        try:
            with self._transport.open(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise WebSkillError("Page fetch returned an unsuccessful HTTP status.")
                content_type = response.headers.get("Content-Type", "").lower()
                if not any(kind in content_type for kind in ("text/html", "text/plain")):
                    raise WebSkillError("Only HTML or plain-text pages are accepted.")
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise WebSkillError("Page exceeds the configured size limit.")
                decoded = body.decode("utf-8", errors="replace")
                decoded = re.sub(r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>", " ", decoded)
                decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
                text = re.sub(r"[ \t\r\n]+", " ", html.unescape(decoded)).strip()
                return {"url": url, "title": text[:160], "text": text[:12000]}
        except PinnedHTTPTransportError:
            raise WebSkillError("Page fetch was blocked or failed by the network firewall.") from None
        except WebSkillError:
            raise
        except (OSError, TimeoutError):
            raise WebSkillError("Page fetch failed or timed out.") from None
