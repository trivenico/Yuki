import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry

from api.oauth_authorization import AuthenticatedOAuthAuthorizationAPI


class FakeAuth:
    def resolve_session(self, token):
        return SimpleNamespace(user_id="owner") if token == "valid-session" else None


class FakeRegistry(SQLiteOAuthConnectionRegistry):
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        super().__init__(str(Path(self._tmp.name) / "registry.sqlite3"))
        self.records = []
        self.fail_register = False
    def register(self, **kwargs):
        if self.fail_register:
            raise RuntimeError("registry unavailable")
        self.records.append(kwargs)
        return SimpleNamespace(
            provider_id=kwargs["provider_id"], account_id=kwargs["account_id"],
            scopes=tuple(kwargs["scopes"]), connected_at=123,
        )


class FakeAuthorization:
    def __init__(self):
        self.begin_calls = []
        self.complete_calls = []
        self.discard_calls = []
        self.result = {
            "provider_id": "provider", "account_id": "account",
            "scopes": ("read",), "credential_version": 1,
        }
    def begin(self, **kwargs):
        self.begin_calls.append(kwargs)
        return {"authorization_url": "https://identity.example/authorize?state=opaque",
                "expires_at": 999}
    def complete(self, **kwargs):
        self.complete_calls.append(kwargs)
        return self.result
    def discard_unregistered_grant(self, **kwargs):
        self.discard_calls.append(kwargs)
        return True


class OAuthAuthorizationAPITests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.service = FakeAuthorization()
        self.app = AuthenticatedOAuthAuthorizationAPI(
            lambda *_: None, FakeAuth(), self.registry,
            lambda provider: self.service if provider == "provider" else None,
        )

    def request(self, method, path, token=None, payload=None):
        sent = []
        raw = json.dumps(payload or {}).encode()
        delivered = False
        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}
        async def send(message):
            sent.append(message)
        headers = [(b"content-type", b"application/json")]
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {"type": "http", "method": method, "path": path, "headers": headers}
        asyncio.run(self.app(scope, receive, send))
        return sent[0]["status"], json.loads(sent[1]["body"])

    def test_provider_catalog_is_authenticated_and_contains_only_safe_fields(self):
        app = AuthenticatedOAuthAuthorizationAPI(
            lambda *_: None, FakeAuth(), self.registry,
            lambda provider: self.service if provider == "provider" else None,
            provider_catalog=lambda: [{
                "provider_id": "provider", "display_name": "Example",
                "available": True, "description": "Read-only access",
                "authorization_url": "https://untrusted.invalid",
            }],
        )
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        async def invoke(token):
            headers = [(b"authorization", ("Bearer " + token).encode())] if token else []
            await app({"type": "http", "method": "GET",
                       "path": "/v1/integrations/oauth/providers", "headers": headers},
                      receive, send)
        asyncio.run(invoke("valid-session"))
        self.assertEqual(sent[0]["status"], 200)
        payload = json.loads(sent[1]["body"])
        self.assertEqual(payload["providers"], [{
            "provider_id": "provider", "display_name": "Example",
            "available": True, "description": "Read-only access",
        }])
        sent.clear()
        asyncio.run(invoke(""))
        self.assertEqual(sent[0]["status"], 401)

    def test_authorization_start_is_bound_to_authenticated_owner_and_session(self):
        status, body = self.request(
            "POST", "/v1/integrations/oauth/providers/provider/authorize",
            "valid-session",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["authorization_url"], "https://identity.example/authorize?state=opaque")
        self.assertEqual(self.service.begin_calls, [{
            "owner_id": "owner", "session_id": "valid-session",
        }])

    def test_callback_registers_only_after_successful_service_completion(self):
        status, body = self.request(
            "POST", "/v1/integrations/oauth/providers/provider/callback",
            "valid-session", {"callback_url": "https://app.example/oauth/callback?code=secret&state=opaque"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["connected"])
        self.assertEqual(self.service.complete_calls[0]["owner_id"], "owner")
        self.assertEqual(self.service.complete_calls[0]["session_id"], "valid-session")
        self.assertEqual(self.registry.records[0]["scopes"], ("read",))

    def test_invalid_session_cannot_start_authorization(self):
        status, _ = self.request(
            "POST", "/v1/integrations/oauth/providers/provider/authorize",
            "invalid-session",
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.service.begin_calls, [])

    def test_unconfigured_provider_is_not_resolved_from_request_data(self):
        status, body = self.request(
            "POST", "/v1/integrations/oauth/providers/attacker/authorize",
            "valid-session",
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "provider_not_configured")

    def test_provider_mismatch_does_not_register_connection(self):
        self.service.result = {
            "provider_id": "other", "account_id": "account", "scopes": ("read",),
        }
        status, _ = self.request(
            "POST", "/v1/integrations/oauth/providers/provider/callback",
            "valid-session", {"callback_url": "https://app.example/oauth/callback?code=secret&state=opaque"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(self.registry.records, [])

    def test_registry_failure_compensates_only_the_stored_credential_generation(self):
        self.registry.fail_register = True
        status, body = self.request(
            "POST", "/v1/integrations/oauth/providers/provider/callback",
            "valid-session", {"callback_url": "https://app.example/oauth/callback?code=secret&state=opaque"},
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "connection_registration_failed")
        self.assertEqual(self.service.discard_calls, [{
            "owner_id": "owner", "account_id": "account", "credential_version": 1,
        }])
        self.assertEqual(self.registry.records, [])

    def test_callback_requires_exact_body_shape(self):
        status, _ = self.request(
            "POST", "/v1/integrations/oauth/providers/provider/callback",
            "valid-session", {"callback_url": "https://app.example/callback", "owner_id": "attacker"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.registry.records, [])


if __name__ == "__main__":
    unittest.main()
