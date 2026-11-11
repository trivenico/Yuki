import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.relationship_settings import AuthenticatedRelationshipSettingsAPI
from companion.relationship_mode import RelationshipMode, RelationshipModeSettings
from companion.relationship_settings_store import RelationshipSettingsStore
from core.auth import BuiltInAuthService


class _Downstream:
    async def __call__(self, scope, receive, send):
        body = b'{"delegated":true}'
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


class AuthenticatedRelationshipSettingsAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "a-secure-owner-password")
        self.owner_one = self.auth.authenticate("owner-one", "a-secure-owner-password")
        self.auth.create_account("owner-two", "another-secure-password")
        self.owner_two = self.auth.authenticate("owner-two", "another-secure-password")
        self.root = root
        self.stores = {}

        def store_for_owner(owner_id):
            return self.stores.setdefault(
                owner_id,
                RelationshipSettingsStore(root / f"{owner_id}.sqlite", owner_id=owner_id),
            )
        self.app = AuthenticatedRelationshipSettingsAPI(_Downstream(), self.auth, store_for_owner)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, payload=None, token=None, path="/v1/companion/relationship-settings"):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            consumed = False
            sent = []
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            await self.app({"type": "http", "method": method, "path": path, "headers": headers}, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"])
        return asyncio.run(run())

    @staticmethod
    def payload(**overrides):
        value = {
            "mode": "friendly", "affection": "moderate", "adult_confirmed": False,
            "memory_enabled": False, "proactive_checkins_enabled": False,
            "quiet_hours_start": None, "quiet_hours_end": None, "topics_to_avoid": [],
        }
        value.update(overrides)
        return value

    def test_requires_authentication_and_delegates_unrelated_routes(self):
        status, body = self.request("GET")
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        status, body = self.request("GET", token=self.owner_one.token, path="/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))

    def test_owner_scoped_get_update_and_reset(self):
        status, body = self.request("GET", token=self.owner_one.token)
        self.assertEqual((status, body["settings"]["mode"]), (200, "off"))
        status, body = self.request(
            "PUT", self.payload(mode="romantic", adult_confirmed=True, topics_to_avoid=["work stress"]),
            self.owner_one.token,
        )
        self.assertEqual((status, body["settings"]["mode"]), (200, "romantic"))
        status, body = self.request("GET", token=self.owner_two.token)
        self.assertEqual((status, body["settings"]["mode"]), (200, "off"))
        status, body = self.request("DELETE", token=self.owner_one.token)
        self.assertEqual((status, body["reset"]), (200, True))
        self.assertEqual(body["settings"]["mode"], "off")

    def test_rejects_owner_override_unknown_fields_and_invalid_consent(self):
        payload = self.payload()
        payload["owner_id"] = self.owner_two.user.user_id
        status, body = self.request("PUT", payload, self.owner_one.token)
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        status, body = self.request(
            "PUT", self.payload(mode="romantic", adult_confirmed=False), self.owner_one.token
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        self.assertEqual(self.stores["owner-one"].load(), RelationshipModeSettings())

    def test_rejects_malformed_payloads_and_missing_quiet_hours(self):
        for payload in [
            {"mode": "friendly"},
            self.payload(proactive_checkins_enabled=True),
            self.payload(topics_to_avoid="not-a-list"),
            self.payload(memory_enabled=1),
        ]:
            status, body = self.request("PUT", payload, self.owner_one.token)
            self.assertEqual((status, body["error"]), (400, "invalid_request"))

    def test_store_resolver_cannot_return_another_owners_store(self):
        other = RelationshipSettingsStore(self.root / "wrong.sqlite", owner_id="owner-two")
        app = AuthenticatedRelationshipSettingsAPI(
            _Downstream(), self.auth, lambda owner_id: other
        )
        previous = self.app
        self.app = app
        try:
            status, body = self.request("GET", token=self.owner_one.token)
            self.assertEqual((status, body["error"]), (500, "storage_unavailable"))
        finally:
            self.app = previous


if __name__ == "__main__":
    unittest.main()
