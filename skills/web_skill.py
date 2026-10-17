"""Bounded HTTP(S) text fetch skill; no arbitrary protocols or local-file URLs."""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class WebSkillError(RuntimeError):
    pass


class WebSkill:
    name = "web_fetch"
    description = "Fetch a small public HTTP(S) page and extract readable text."
    risk = "medium"
    requires_approval = True

    def __init__(self, *, allowed_hosts=(), timeout: int = 8, max_bytes: int = 500_000):
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.timeout, self.max_bytes = timeout, max_bytes

    def execute(self, url: str) -> dict:
        if not isinstance(url, str) or len(url) > 2048:
            raise WebSkillError("URL must be a bounded string.")
        parts = urlsplit(url.strip())
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise WebSkillError("Only HTTPS URLs without embedded credentials are accepted.")
        host = parts.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise WebSkillError("Local hosts are blocked.")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise WebSkillError("Host is not on the configured allow-list.")
        request = urllib.request.Request(url, headers={"User-Agent": "Yuki/0.1 (user-initiated fetch)", "Accept": "text/html,text/plain"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "https" or final.hostname != host:
                    raise WebSkillError("Redirect to another host or protocol was blocked.")
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "text/plain"}:
                    raise WebSkillError("Only HTML and plain text are supported.")
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise WebSkillError("Page exceeds the configured size limit.")
                text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                if content_type == "text/html":
                    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", text)
                    text = re.sub(r"(?s)<[^>]+>", " ", text)
                    text = html.unescape(text)
                return {"url": url, "text": re.sub(r"\s+", " ", text).strip()[:20000]}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WebSkillError("Page fetch failed.") from exc
