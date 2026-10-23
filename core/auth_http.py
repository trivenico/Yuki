"""Framework-neutral ASGI authentication and cookie/CSRF helpers for hosted Yuki.

The middleware only authenticates requests. Login/account endpoints remain host-owned.
Use HTTPS in hosted deployments. Setting secure_cookies=False is an explicit local
development escape hatch and must never be used for a public deployment.
"""
from __future__ import annotations

from http.cookies import SimpleCookie
import hmac
import secrets
from typing import Callable

from core.auth import AuthenticationError, BuiltInAuthService

SESSION_COOKIE = "yuki_session"
CSRF_COOKIE = "yuki_csrf"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def issue_session_cookie_headers(session, *, secure_cookies: bool = True) -> list[tuple[bytes, bytes]]:
    """Return Set-Cookie headers for an IssuedSession and a separate CSRF token."""
    token = getattr(session, "token", None)
    expires_at = getattr(session, "expires_at", None)
    if not isinstance(token, str) or not token or not isinstance(expires_at, str):
        raise ValueError("a valid issued session is required.")
    csrf = secrets.token_urlsafe(32)
    secure = "; Secure" if secure_cookies else ""
    max_age = max(0, int(getattr(session, "max_age_seconds", 0) or 0))
    # Do not accept caller-supplied cookie attributes or token fragments.
    session_value = (
        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict{secure}"
    )
    csrf_value = f"{CSRF_COOKIE}={csrf}; Path=/; SameSite=Strict{secure}"
    headers = [
        (b"set-cookie", session_value.encode("ascii")),
        (b"set-cookie", csrf_value.encode("ascii")),
        (b"cache-control", b"no-store"),
        (b"pragma", b"no-cache"),
    ]
    return headers


def clear_session_cookie_headers(*, secure_cookies: bool = True) -> list[tuple[bytes, bytes]]:
    secure = "; Secure" if secure_cookies else ""
    return [
        (b"set-cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{secure}".encode("ascii")),
        (b"set-cookie", f"{CSRF_COOKIE}=; Path=/; Max-Age=0; SameSite=Strict{secure}".encode("ascii")),
        (b"cache-control", b"no-store"),
    ]


def _cookies(scope) -> dict[str, str]:
    values = {}
    for key, value in scope.get("headers", []):
        if key.lower() == b"cookie":
            try:
                parsed = SimpleCookie()
                parsed.load(value.decode("latin-1"))
                values.update({name: morsel.value for name, morsel in parsed.items()})
            except (UnicodeError, ValueError):
                return {}
    return values


class YukiAuthMiddleware:
    """ASGI middleware: resolve session, enforce CSRF on unsafe cookie-auth requests."""

    def __init__(
        self,
        app,
        auth_service: BuiltInAuthService,
        *,
        secure_cookies: bool = True,
        on_auth_error: Callable[[str], None] | None = None,
    ):
        if not callable(app) or not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("app and BuiltInAuthService are required.")
        if not isinstance(secure_cookies, bool):
            raise TypeError("secure_cookies must be boolean.")
        self.app = app
        self.auth_service = auth_service
        self.secure_cookies = secure_cookies
        self.on_auth_error = on_auth_error

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})
        cookies = _cookies(scope)
        token = cookies.get(SESSION_COOKIE)
        principal = None
        if token:
            try:
                principal = self.auth_service.resolve_session_principal(token)
            except AuthenticationError:
                principal = None
            except Exception:
                principal = None
        scope["yuki.principal"] = principal
        scope["state"]["yuki_principal"] = principal
        method = str(scope.get("method", "GET")).upper()
        if principal is not None and method not in _SAFE_METHODS:
            csrf_cookie = cookies.get(CSRF_COOKIE, "")
            csrf_header = ""
            for key, value in scope.get("headers", []):
                if key.lower() == b"x-csrf-token":
                    try:
                        csrf_header = value.decode("ascii")
                    except UnicodeError:
                        csrf_header = ""
                    break
            if (
                not csrf_cookie or not csrf_header
                or len(csrf_cookie) > 256 or len(csrf_header) > 256
                or not hmac.compare_digest(csrf_cookie, csrf_header)
            ):
                await self._respond(send, 403, b'{"error":"csrf_validation_failed"}')
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _respond(send, status: int, body: bytes):
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def principal_from_scope(scope):
    """Read the principal injected by middleware; never trust request JSON for identity."""
    principal = scope.get("yuki.principal")
    if principal is None:
        raise AuthenticationError("Authentication required.")
    return principal
