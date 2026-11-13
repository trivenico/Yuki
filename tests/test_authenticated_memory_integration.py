import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.conversations import YukiConversationAPI
from core.agent_execution_loop import AgentExecutionLoop
from core.auth import BuiltInAuthService
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from core.user_memory_service import UserMemoryService
from core.memory_context import MemoryContextBuilder
from core.cognitive_context import CognitiveContextEngine


class NoApprovals:
    def authorize_tool(self, name):
        return False


class AuthenticatedMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner", "a-secure-test-password")
        self.session = self.auth.authenticate("owner", "a-secure-test-password")
        self.memory = UserMemoryService(root / "memory", root / "consent.sqlite")
        self.seen_messages = []

        def model(*, messages, tools, tool_choice):
            self.seen_messages.append(messages)
            return {"text": "memory-aware answer", "tool_calls": {"tool_calls": []}}

        loop = AgentExecutionLoop(
            model, StructuredToolProtocol(GuardedToolRuntime({}, NoApprovals()))
        )
        self.store = ConversationStore(root / "conversations.sqlite")
        self.conversations = ConversationService(
            self.store, loop,
            memory_context_builder=MemoryContextBuilder(self.memory),
            cognitive_context_engine=CognitiveContextEngine(
                memory_context_builder=MemoryContextBuilder(self.memory)
            ),
        )
        self.app = YukiConversationAPI(self.auth, self.conversations, memory_service=self.memory)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, payload=None, token=None):
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

            headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
            await self.app(
                {"type": "http", "method": method, "path": path, "headers": headers, "query_string": b""},
                receive, send,
            )
            return messages[0]["status"], json.loads(messages[1]["body"])
        return asyncio.run(run())

    def test_consent_is_authenticated_owner_scoped_and_gates_memory(self):
        status, data = self.request("GET", "/v1/memory/consent", token=self.session.token)
        self.assertEqual((status, data), (200, {"enabled": False}))
        status, denied = self.request(
            "POST", "/v1/memory", {"text": "must not persist without consent"}, self.session.token
        )
        self.assertEqual((status, denied), (403, {"error": "memory_consent_required"}))

        status, _ = self.request(
            "POST", "/v1/memory",
            {"text": "Yuki should prefer local inference", "owner_id": "victim"},
            self.session.token,
        )
        self.assertEqual(status, 400)

        status, data = self.request(
            "PUT", "/v1/memory/consent", {"enabled": True}, self.session.token
        )
        self.assertEqual(status, 200)
        status, created = self.request(
            "POST", "/v1/memory",
            {"text": "Yuki should prefer local inference", "category": "preference"},
            self.session.token,
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["memory"]["text"], "Yuki should prefer local inference")

        conversation = self.conversations.create(owner_id=self.session.user.user_id)
        status, reply = self.request(
            "POST", f"/v1/conversations/{conversation['conversation_id']}/messages",
            {"content": "What model setup should I use?", "request_id": "memory-turn-1", "expected_version": 0},
            self.session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["answer"], "memory-aware answer")
        system_message = self.seen_messages[-1][0]["content"]
        self.assertIn("YUKI COGNITIVE BRIEF", system_message)
        self.assertIn("UNTRUSTED REFERENCE DATA ONLY", system_message)
        self.assertIn("prefer local inference", system_message)

        status, data = self.request(
            "PUT", "/v1/memory/consent", {"enabled": False}, self.session.token
        )
        self.assertEqual(status, 200)
        second = self.conversations.create(owner_id=self.session.user.user_id)
        status, _ = self.request(
            "POST", f"/v1/conversations/{second['conversation_id']}/messages",
            {"content": "Repeat", "request_id": "memory-turn-2", "expected_version": 0},
            self.session.token,
        )
        self.assertEqual(status, 200)
        self.assertNotIn("prefer local inference", self.seen_messages[-1][0]["content"])
        memory_id = created["memory"]["id"]
        status, deleted = self.request(
            "DELETE", f"/v1/memory/{memory_id}", token=self.session.token
        )
        self.assertEqual((status, deleted), (200, {"deleted": True}))

    def test_memory_endpoints_require_authentication(self):
        status, data = self.request("GET", "/v1/memory/consent")
        self.assertEqual(status, 401)
        status, data = self.request("GET", "/v1/memory")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
