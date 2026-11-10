import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.auth import AuthenticationError, BuiltInAuthService
from core.auth_http import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    YukiAuthMiddleware,
    clear_session_cookie_headers,
    issue_session_cookie_headers,
    principal_from_scope,
)


class AuthHttpMiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
        )
        self.auth.bootstrap_admin("root.user", "A sufficiently long password 7!")
        self.session = self.auth.authenticate("root.user", "A sufficiently long password 7!")
        self.app_calls = []

        async def app(scope, receive, send):
            self.app_calls.append(scope)
            body = b'{"ok":true}'
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        self.app = app
        self.middleware = YukiAuthMiddleware(app, self.auth)

    def request(self, method="GET", cookie="", csrf=""):
        headers = []
        if cookie:
            headers.append((b"cookie", cookie.encode("ascii")))
        if csrf:
            headers.append((b"x-csrf-token", csrf.encode("ascii")))
        scope = {"type": "http", "method": method, "headers": headers, "state": {}}
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(self.middleware(scope, receive, send))
        return scope, sent

    def test_middleware_injects_trusted_principal_and_session_id_is_not_token(self):
        scope, sent = self.request(cookie=f"{SESSION_COOKIE}={self.session.token}")
        principal = principal_from_scope(scope)
        self.assertEqual(principal.actor_id, self.session.user.user_id)
        self.assertNotEqual(principal.session_id, self.session.token)
        self.assertEqual(sent[0]["status"], 200)

    def test_unsafe_cookie_authenticated_request_requires_matching_csrf(self):
        scope, sent = self.request("POST", f"{SESSION_COOKIE}={self.session.token}")
        self.assertEqual(sent[0]["status"], 403)
        self.assertEqual(self.app_calls, [])
        csrf = "csrf-test-value"
        scope, sent = self.request(
            "POST",
            f"{SESSION_COOKIE}={self.session.token}; {CSRF_COOKIE}={csrf}",
            csrf=csrf,
        )
        self.assertEqual(sent[0]["status"], 200)
        self.assertIsNotNone(scope["yuki.principal"])

    def test_missing_or_revoked_session_is_anonymous_and_route_must_require_auth(self):
        scope, _ = self.request(cookie=f"{SESSION_COOKIE}=not-a-real-session-token-123456789")
        with self.assertRaises(AuthenticationError):
            principal_from_scope(scope)
        self.auth.revoke_session(self.session.token)
        scope, _ = self.request(cookie=f"{SESSION_COOKIE}={self.session.token}")
        self.assertIsNone(scope["yuki.principal"])

    def test_cookie_headers_are_secure_by_default_and_logout_clears_them(self):
        headers = issue_session_cookie_headers(self.session)
        cookies = [value.decode() for key, value in headers if key == b"set-cookie"]
        self.assertEqual(len(cookies), 2)
        self.assertIn("HttpOnly", cookies[0])
        self.assertIn("Secure", cookies[0])
        self.assertIn("SameSite=Strict", cookies[0])
        self.assertNotIn("HttpOnly", cookies[1])
        cleared = [value.decode() for key, value in clear_session_cookie_headers() if key == b"set-cookie"]
        self.assertEqual(len(cleared), 2)
        self.assertTrue(all("Max-Age=0" in value and "Secure" in value for value in cleared))

    def test_session_principal_has_permissions_needed_by_approval_service(self):
        principal = self.auth.resolve_session_principal(self.session.token)
        self.assertIn("workflow:approve", principal.permissions)
        self.assertNotEqual(principal.session_id, self.session.token)


if __name__ == "__main__":
    unittest.main()
