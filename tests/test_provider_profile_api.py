import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.provider_profiles import AuthenticatedProviderProfileAPI
from core.auth import BuiltInAuthService
from providers.profile_store import ProviderProfileStore


class MemoryVault:
    def __init__(self):
        self.values = {}
    def set_secret(self, reference, secret):
        self.values[reference] = secret
    def get_secret(self, reference):
        return self.values.get(reference)
    def delete_secret(self, reference):
        self.values.pop(reference, None)


class Downstream:
    async def __call__(self, scope, receive, send):
        body = b'{"delegated":true}'
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


class ProviderProfileAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "a-secure-owner-password")
        self.session = self.auth.authenticate("owner-one", "a-secure-owner-password")
        self.auth.create_account("owner-two", "another-secure-password")
        self.other = self.auth.authenticate("owner-two", "another-secure-password")
        self.vault = MemoryVault()
        self.store = ProviderProfileStore(root / "profiles.sqlite", self.vault)
        self.app = AuthenticatedProviderProfileAPI(Downstream(), self.auth, self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, token=None):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            consumed, sent = False, []
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
            if payload is not None:
                headers.append((b"content-type", b"application/json"))
            await self.app({"type": "http", "method": method, "path": path,
                            "headers": headers, "query_string": b""}, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"]), dict(sent[0]["headers"])
        return asyncio.run(run())

    def profile_payload(self, **overrides):
        value = {
            "name": "gateway", "base_url": "https://gateway.example.test/v1",
            "model": "vendor/model", "approved_host": "gateway.example.test",
            "api_key": "do-not-return-this-secret", "confirm_cloud": True,
        }
        value.update(overrides)
        return value

    def test_all_routes_require_authenticated_owner_session(self):
        status, body, _ = self.request("GET", "/v1/provider-profiles")
        self.assertEqual((status, body["error"]), (401, "authentication_required"))

    def test_create_lists_and_verifies_without_exposing_secret(self):
        status, body, headers = self.request(
            "POST", "/v1/provider-profiles", self.profile_payload(), self.session.token
        )
        self.assertEqual(status, 201)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertNotIn("do-not-return-this-secret", json.dumps(body))
        status, body, _ = self.request("GET", "/v1/provider-profiles", token=self.session.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["profiles"][0]["name"], "gateway")
        self.assertTrue(body["profiles"][0]["credential_configured"])
        self.assertNotIn("api_key", body["profiles"][0])
        status, body, _ = self.request(
            "POST", "/v1/provider-profiles/gateway/verify", {}, self.session.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["verification"], "local_vault_only")
        self.assertFalse(body["network_probe_performed"])

    def test_profiles_and_selection_are_owner_scoped(self):
        self.request("POST", "/v1/provider-profiles", self.profile_payload(), self.session.token)
        status, body, _ = self.request("GET", "/v1/provider-profiles", token=self.other.token)
        self.assertEqual((status, body["profiles"]), (200, []))
        status, body, _ = self.request(
            "PUT", "/v1/provider-profiles/active", {"name": "gateway"}, self.other.token
        )
        self.assertEqual((status, body["error"]), (404, "profile_not_found"))
        status, body, _ = self.request(
            "PUT", "/v1/provider-profiles/active", {"name": "gateway"}, self.session.token
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["active_profile"], "gateway")
        status, body, _ = self.request(
            "GET", "/v1/provider-profiles/active", token=self.session.token
        )
        self.assertEqual(body["active_profile"]["name"], "gateway")

    def test_selection_fails_closed_when_vault_credential_is_missing(self):
        self.request("POST", "/v1/provider-profiles", self.profile_payload(), self.session.token)
        self.vault.values.clear()
        status, body, _ = self.request(
            "PUT", "/v1/provider-profiles/active", {"name": "gateway"}, self.session.token
        )
        self.assertEqual((status, body["error"]), (409, "profile_credential_unavailable"))
        self.assertIsNone(self.store.selected_profile(self.session.user.user_id))

    def test_cloud_consent_and_exact_payload_are_mandatory(self):
        status, body, _ = self.request(
            "POST", "/v1/provider-profiles",
            self.profile_payload(confirm_cloud=False), self.session.token
        )
        self.assertEqual((status, body["error"]), (400, "explicit_cloud_confirmation_required"))
        status, body, _ = self.request(
            "POST", "/v1/provider-profiles",
            self.profile_payload(owner_id=self.other.user.user_id), self.session.token
        )
        self.assertEqual((status, body["error"]), (400, "explicit_cloud_confirmation_required"))

    def test_delete_is_owner_scoped_and_clears_selection(self):
        self.request("POST", "/v1/provider-profiles", self.profile_payload(), self.session.token)
        self.request("PUT", "/v1/provider-profiles/active", {"name": "gateway"}, self.session.token)
        status, body, _ = self.request(
            "DELETE", "/v1/provider-profiles/gateway", token=self.other.token
        )
        self.assertEqual(status, 404)
        status, body, _ = self.request(
            "DELETE", "/v1/provider-profiles/gateway", token=self.session.token
        )
        self.assertEqual((status, body["deleted"]), (200, True))
        self.assertIsNone(self.store.selected_profile(self.session.user.user_id))

    def test_unrelated_routes_are_delegated(self):
        status, body, _ = self.request("GET", "/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))


if __name__ == "__main__":
    unittest.main()
