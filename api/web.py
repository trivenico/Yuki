"""Minimal same-origin web shell for the Yuki conversation API."""
from __future__ import annotations

import json
import re
from pathlib import Path

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
    "/assets/chat.css": ("chat.css", "text/css; charset=utf-8"),
    "/assets/annotation.js": ("annotation.js", "text/javascript; charset=utf-8"),
    "/assets/annotation.css": ("annotation.css", "text/css; charset=utf-8"),
}
_WEB_ROOT = Path(__file__).resolve().parent.parent / "ui" / "web"
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
]


class YukiWebApplication:
    """Serve the allowlisted static chat shell and delegate API traffic."""

    def __init__(self, api):
        self.api = api

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.api(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        if path == "/healthz" and method in {"GET", "HEAD"}:
            body = json.dumps({"status": "ok", "service": "yuki"}).encode("utf-8")
            await self._respond(send, 200, body, "application/json; charset=utf-8", method)
            return
        callback = re.fullmatch(
            r"/oauth/callback/([A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127})", path
        )
        if callback is not None:
            if method not in {"GET", "HEAD"}:
                await self._respond(send, 405, b"", "text/plain; charset=utf-8", method,
                                    extra=[(b"allow", b"GET, HEAD")])
                return
            try:
                body = (_WEB_ROOT / "index.html").read_bytes()
            except OSError:
                await self._respond(send, 503, b"Web interface unavailable.", "text/plain; charset=utf-8", method)
                return
            await self._respond(send, 200, body, "text/html; charset=utf-8", method, cache=b"no-store")
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
