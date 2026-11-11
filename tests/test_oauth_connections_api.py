import asyncio
import json
import unittest
from types import SimpleNamespace

from api.oauth_connections import AuthenticatedOAuthConnectionAPI


class FakeAuth:
    def resolve_session(self, token):
        return SimpleNamespace(user_id="owner") if token == "valid-session" else None


class FakeRegistry:
    def __init__(self):
        self.records = {("owner", "provider", "account"): SimpleNamespace(
            provider_id="provider", account_id="account", scopes=("read",), connected_at=100
        )}
    def list_for_owner(self, *, owner_id):
        return list(self.records.values()) if owner_id == "owner" else []
    def get(self, *, owner_id, provider_id, account_id):
        return self.records.get((owner_id, provider_id, account_id))
    def remove(self, *, owner_id, provider_id, account_id):
        return self.records.pop((owner_id, provider_id, account_id), None) is not None


class FakeDisconnect:
    def __init__(self, result=None):
        self.result = result or {"disconnected": True, "revocation_attempted": True, "remote_revoked": False}
        self.calls = []
    def disconnect(self, *, owner_id, account_id):
        self.calls.append((owner_id, account_id))
        return self.result


class OAuthConnectionAPITests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.disconnect = FakeDisconnect()
        self.app = AuthenticatedOAuthConnectionAPI(
            lambda *_: None, FakeAuth(), self.registry,
            lambda provider: self.disconnect if provider == "provider" else None,
        )

    def request(self, method, path, token=None):
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "method": method, "path": path, "headers":
                 ([(b"authorization", f"Bearer {token}".encode())] if token else [])}
        asyncio.run(self.app(scope, receive, send))
        start = sent[0]
        body = json.loads(sent[1]["body"])
        return start["status"], body

    def test_requires_authenticated_owner_session(self):
        status, body = self.request("GET", "/v1/integrations/oauth/connections")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "authentication_required")

    def test_lists_only_non_secret_owner_metadata(self):
        status, body = self.request("GET", "/v1/integrations/oauth/connections", "valid-session")
        self.assertEqual(status, 200)
        self.assertEqual(body["connections"][0]["provider_id"], "provider")
        self.assertNotIn("access_token", str(body))
        self.assertNotIn("refresh_token", str(body))

    def test_disconnects_locally_even_if_remote_revocation_failed(self):
        status, body = self.request(
            "DELETE", "/v1/integrations/oauth/connections/provider/account", "valid-session"
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["disconnected"])
        self.assertFalse(body["remote_revoked"])
        self.assertEqual(self.registry.records, {})

    def test_unknown_owner_connection_is_not_disconnected(self):
        status, body = self.request(
            "DELETE", "/v1/integrations/oauth/connections/provider/other", "valid-session"
        )
        self.assertEqual(status, 404)
        self.assertEqual(self.disconnect.calls, [])

    def test_failed_disconnect_keeps_registry_entry(self):
        self.disconnect.result = {"disconnected": False}
        status, _ = self.request(
            "DELETE", "/v1/integrations/oauth/connections/provider/account", "valid-session"
        )
        self.assertEqual(status, 502)
        self.assertTrue(self.registry.records)


if __name__ == "__main__":
    unittest.main()
