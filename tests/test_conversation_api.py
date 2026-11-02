import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.conversations import YukiConversationAPI
from core.auth import BuiltInAuthService
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class _NoApprovals:
    def authorize_tool(self, name):
        return False


class ConversationAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.user = self.auth.bootstrap_admin("admin-user", "a-secure-test-password")
        self.issued = self.auth.authenticate("admin-user", "a-secure-test-password")

        def model(**kwargs):
            return {"text": "Hello from Yuki", "tool_calls": {"tool_calls": []}}

        protocol = StructuredToolProtocol(GuardedToolRuntime({}, _NoApprovals()))
        loop = AgentExecutionLoop(model, protocol)
        service = ConversationService(ConversationStore(root / "conversations.sqlite"), loop)
        self.app = YukiConversationAPI(self.auth, service)

    def tearDown(self):
        self.tempdir.cleanup()

    def request(self, method, path, payload=None, token=None, query=b""):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            sent = False
            messages = []
            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                messages.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            scope = {
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": query,
            }
            await self.app(scope, receive, send)
            status = messages[0]["status"]
            data = json.loads(messages[1]["body"])
            response_headers = dict(messages[0]["headers"])
            return status, data, response_headers
        return asyncio.run(run())

    def test_login_create_send_history_and_logout(self):
        status, data, headers = self.request(
            "POST", "/v1/auth/login",
            {"username": "admin-user", "password": "a-secure-test-password"},
        )
        self.assertEqual(status, 200)
        token = data["access_token"]
        self.assertEqual(headers[b"cache-control"], b"no-store")

        status, conversation, _ = self.request(
            "POST", "/v1/conversations", {"title": "API test"}, token
        )
        self.assertEqual(status, 201)
        cid = conversation["conversation_id"]

        status, reply, _ = self.request(
            "POST", f"/v1/conversations/{cid}/messages",
            {"content": "Hello", "request_id": "api-request-1", "expected_version": 0},
            token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["answer"], "Hello from Yuki")

        status, history, _ = self.request("GET", f"/v1/conversations/{cid}/messages", token=token)
        self.assertEqual(status, 200)
        self.assertEqual([item["role"] for item in history["messages"]], ["user", "assistant"])

        status, _, _ = self.request("POST", "/v1/auth/logout", token=token)
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/v1/conversations", token=token)
        self.assertEqual(status, 401)

    def test_unauthenticated_and_forged_owner_fields_are_rejected(self):
        status, _, _ = self.request("GET", "/v1/conversations")
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "POST", "/v1/conversations",
            {"title": "Not allowed", "owner_id": "victim"},
            self.issued.token,
        )
        self.assertEqual(status, 400)

    def test_invalid_login_has_generic_error(self):
        status, data, _ = self.request(
            "POST", "/v1/auth/login",
            {"username": "admin-user", "password": "incorrect-password"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(data, {"error": "invalid_credentials"})

    def test_unknown_conversation_does_not_leak_existence(self):
        status, data, _ = self.request(
            "GET", "/v1/conversations/00000000-0000-4000-8000-000000000000/messages",
            token=self.issued.token,
        )
        self.assertEqual(status, 404)
        self.assertEqual(data, {"error": "not_found"})

    def test_oversized_body_is_rejected(self):
        async def run():
            events = [
                {"type": "http.request", "body": b"x" * 24_001, "more_body": False}
            ]
            async def receive():
                return events.pop(0)
            sent = []
            async def send(message):
                sent.append(message)
            await self.app(
                {"type": "http", "method": "POST", "path": "/v1/auth/login",
                 "headers": [], "query_string": b""},
                receive, send,
            )
            return sent[0]["status"]
        self.assertEqual(asyncio.run(run()), 400)


if __name__ == "__main__":
    unittest.main()
