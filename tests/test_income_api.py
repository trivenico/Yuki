import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.income import AuthenticatedIncomeOpportunityAPI
from core.auth import BuiltInAuthService
from core.income_opportunities import SQLiteIncomeOpportunityStore


class _Downstream:
    async def __call__(self, scope, receive, send):
        body = b'{"delegated":true}'
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


class AuthenticatedIncomeOpportunityAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "a-secure-owner-password")
        self.owner_one = self.auth.authenticate("owner-one", "a-secure-owner-password")
        self.auth.create_account("owner-two", "another-secure-password")
        self.owner_two = self.auth.authenticate("owner-two", "another-secure-password")
        self.store = SQLiteIncomeOpportunityStore(str(root / "income.sqlite"))
        self.app = AuthenticatedIncomeOpportunityAPI(_Downstream(), self.auth, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, payload=None, token=None, query=b""):
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
            await self.app({
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": query,
            }, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"])
        return asyncio.run(run())

    def create_payload(self):
        return {
            "platform_id": "sample-market",
            "title": "Remote design project",
            "category": "freelance",
            "source_url": "https://example.com/jobs/123",
            "evidence_level": "multiple_sources",
            "currency": "GBP",
            "estimated_net_minor": 25000,
        }

    def test_authentication_required_and_owner_id_cannot_be_supplied(self):
        status, body = self.request("GET", "/v1/income/opportunities")
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        payload = self.create_payload()
        payload["owner_id"] = self.owner_two.user.user_id
        status, body = self.request(
            "POST", "/v1/income/opportunities", payload, self.owner_one.token
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        self.assertEqual(self.store.list(self.owner_one.user.user_id), [])

    def test_owner_scoped_create_list_get_transition_and_delete(self):
        status, body = self.request(
            "POST", "/v1/income/opportunities", self.create_payload(), self.owner_one.token
        )
        self.assertEqual(status, 201)
        item = body["opportunity"]
        opportunity_id = item["opportunity_id"]
        self.assertEqual(item["status"], "discovered")
        self.assertEqual(item["estimated_net_minor"], 25000)

        status, body = self.request(
            "GET", "/v1/income/opportunities", token=self.owner_two.token
        )
        self.assertEqual((status, body["opportunities"]), (200, []))
        status, body = self.request(
            "GET", f"/v1/income/opportunities/{opportunity_id}", token=self.owner_two.token
        )
        self.assertEqual((status, body["error"]), (404, "not_found"))

        status, body = self.request(
            "GET", "/v1/income/opportunities",
            token=self.owner_one.token, query=b"status=discovered&platform_id=sample-market",
        )
        self.assertEqual((status, len(body["opportunities"])), (200, 1))

        status, body = self.request(
            "POST", f"/v1/income/opportunities/{opportunity_id}/status",
            {"new_status": "researching"}, self.owner_one.token,
        )
        self.assertEqual((status, body["opportunity"]["status"]), (200, "researching"))

        status, body = self.request(
            "DELETE", f"/v1/income/opportunities/{opportunity_id}", token=self.owner_two.token
        )
        self.assertEqual((status, body["error"]), (404, "not_found"))
        status, body = self.request(
            "DELETE", f"/v1/income/opportunities/{opportunity_id}", token=self.owner_one.token
        )
        self.assertEqual((status, body["deleted"]), (200, True))

    def test_unknown_fields_duplicate_query_and_invalid_transition_rejected(self):
        payload = self.create_payload()
        payload["arbitrary"] = "ignored?"
        status, body = self.request(
            "POST", "/v1/income/opportunities", payload, self.owner_one.token
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))

        status, body = self.request(
            "GET", "/v1/income/opportunities", token=self.owner_one.token,
            query=b"status=discovered&status=active",
        )
        self.assertEqual((status, body["error"]), (400, "invalid_query"))

        status, body = self.request(
            "POST", "/v1/income/opportunities", self.create_payload(), self.owner_one.token
        )
        opportunity_id = body["opportunity"]["opportunity_id"]
        status, body = self.request(
            "POST", f"/v1/income/opportunities/{opportunity_id}/status",
            {"new_status": "active"}, self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))

    def test_unrelated_routes_are_delegated(self):
        status, body = self.request("GET", "/healthz")
        self.assertEqual((status, body["delegated"]), (200, True))


if __name__ == "__main__":
    unittest.main()
