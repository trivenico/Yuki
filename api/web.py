"""Minimal same-origin web shell for the Yuki conversation API."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
    "/assets/chat.css": ("chat.css", "text/css; charset=utf-8"),
}
_WEB_ROOT = Path(__file__).resolve().parent.parent / "ui" / "web"
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
]


class YukiWebApplication:
    """Serve the allowlisted static chat shell and delegate API traffic."""

    def __init__(self, api, *, allowed_hosts=None):
        self.api = api
        configured = allowed_hosts
        if configured is None:
            configured = os.environ.get(
                "YUKI_WEB_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"
            ).split(",")
        if isinstance(configured, str):
            configured = configured.split(",")
        normalized = {
            str(host).strip().lower().rstrip(".")
            for host in configured if str(host).strip()
        }
        if not normalized or "*" in normalized:
            raise ValueError("allowed_hosts must contain explicit hostnames; wildcards are forbidden.")
        self.allowed_hosts = frozenset(normalized)

    @staticmethod
    def _header_values(scope, name):
        target = name.lower().encode("ascii")
        return [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == target
        ]

    @staticmethod
    def _hostname(authority):
        if not isinstance(authority, str) or not authority or any(
            char.isspace() for char in authority
        ) or "," in authority or "@" in authority:
            return None
        try:
            parsed = urlsplit("//" + authority)
            if parsed.path or parsed.query or parsed.fragment:
                return None
            return parsed.hostname.lower().rstrip(".") if parsed.hostname else None
        except ValueError:
            return None

    def _request_rejection(self, scope, method):
        host_values = self._header_values(scope, "host")
        # ASGI servers must supply exactly one Host authority. Forwarded host
        # headers are deliberately ignored; the trusted proxy must validate Host.
        if len(host_values) != 1:
            return 400, "invalid_host"
        host = self._hostname(host_values[0])
        try:
            host_port = urlsplit("//" + host_values[0]).port
        except ValueError:
            return 400, "invalid_host"
        if host is None or host not in self.allowed_hosts:
            return 421, "untrusted_host"

        if method not in {"GET", "HEAD", "OPTIONS"}:
            if any(
                value.strip().lower() == "cross-site"
                for value in self._header_values(scope, "sec-fetch-site")
            ):
                return 403, "cross_site_request_denied"
            origins = self._header_values(scope, "origin")
            if len(origins) > 1:
                return 403, "invalid_origin"
            if origins:
                try:
                    origin = urlsplit(origins[0])
                    origin_host = origin.hostname.lower().rstrip(".") if origin.hostname else None
                    origin_port = origin.port
                    valid_scheme = origin.scheme in {"http", "https"}
                    valid_authority = bool(origin.netloc) and not origin.username and not origin.password
                    no_path = origin.path in {"", "/"} and not origin.query and not origin.fragment
                    effective_origin_port = origin_port or (443 if origin.scheme == "https" else 80)
                    effective_host_port = host_port or effective_origin_port
                except ValueError:
                    return 403, "invalid_origin"
                if (
                    not valid_scheme or not valid_authority or not no_path
                    or origin_host != host or origin_host not in self.allowed_hosts
                    or effective_origin_port != effective_host_port
                ):
                    return 403, "origin_not_allowed"
        return None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.api(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        rejection = self._request_rejection(scope, method)
        if rejection is not None:
            status, code = rejection
            await self._respond(
                send, status, json.dumps({"error": code}).encode("utf-8"),
                "application/json; charset=utf-8", method,
            )
            return
        if path == "/healthz" and method in {"GET", "HEAD"}:
            body = json.dumps({"status": "ok", "service": "yuki"}).encode("utf-8")
            await self._respond(send, 200, body, "application/json; charset=utf-8", method)
            return
        asset = _ASSETS.get(path)
        if asset is not None:
            if method not in {"GET", "HEAD"}:
                await self._respond(send, 405, b"", "text/plain; charset=utf-8", method,
                                    extra=[(b"allow", b"GET, HEAD")])
                return
            filename, content_type = asset
            try:
                body = (_WEB_ROOT / filename).read_bytes()
            except OSError:
                await self._respond(send, 503, b"Web interface unavailable.", "text/plain; charset=utf-8", method)
                return
            cache = b"no-store" if filename == "index.html" else b"public, max-age=300"
            await self._respond(send, 200, body, content_type, method, cache=cache)
            return
        await self.api(scope, receive, send)

    @staticmethod
    async def _respond(send, status, body, content_type, method, *, cache=b"no-store", extra=None):
        headers = [
            (b"content-type", content_type.encode("ascii")),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", cache),
            *_SECURITY_HEADERS,
        ]
        if extra:
            headers.extend(extra)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else body})
