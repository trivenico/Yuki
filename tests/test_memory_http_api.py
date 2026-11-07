"""HTTP-boundary tests for owner-authenticated memory review and lifecycle."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from api.conversations import YukiConversationAPI
from core.agent_execution_loop import AgentExecutionLoop
from core.agent_memory import AgentMemoryStore
from core.auth import BuiltInAuthService
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from core.owner_identity import memory_actor_id_for_owner


class _DenyApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "test response", "tool_calls": {"tool_calls": []}}
    return AgentExecutionLoop(
        model, StructuredToolProtocol(GuardedToolRuntime({}, _DenyApprovals()))
    )


class MemoryHttpApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.grants = {}
        self.memory = AgentMemoryStore(
            str(root / "memory.sqlite3"),
            authorize=lambda actor, action, resource: action in self.grants.get(actor, set()),
        )
        self.auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
        self.auth.bootstrap_admin("memory-owner", "a-secure-memory-password")
        self.session = self.auth.authenticate("memory-owner", "a-secure-memory-password")
        actor = memory_actor_id_for_owner(self.session.user.user_id)
        self.grants[actor] = {
            "agent_memory:read", "agent_memory:write", "agent_memory:review",
            "agent_memory:retire", "agent_memory:correct", "agent_memory:delete",
        }
        self.conversation_store = ConversationStore(root / "conversations.sqlite3")
        self.service = ConversationService(self.conversation_store, _loop())
        self.api = YukiConversationAPI(
            self.auth, self.service, memory_store=self.memory,
        )

    def request(self, method, path, payload=None, *, token=None):
        async def invoke():
            body = json.dumps(payload).encode() if payload is not None else b""
            sent = []
            consumed = False
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = [(b"host", b"localhost")]
            if token:
                headers.append((b"authorization", ("Bearer " + token).encode()))
            await self.api(
                {"type": "http", "method": method, "path": path,
                 "headers": headers, "query_string": b""},
                receive, send,
            )
            status = sent[0]["status"]
            raw = sent[1].get("body", b"")
            return status, json.loads(raw) if raw else {}
        return asyncio.run(invoke())

    def test_candidate_requires_authenticated_owner_approval_and_is_idempotent(self):
        conversation = self.service.create(owner_id=self.session.user.user_id)
        conversation_id = conversation["conversation_id"]
        self.service.reply(
            owner_id=self.session.user.user_id, conversation_id=conversation_id,
            user_text="I prefer concise technical explanations.",
            request_id="memory-http-1", expected_version=0, tools=[],
        )
        status, result = self.request(
            "GET", "/v1/memory/candidates?conversation_id=" + conversation_id,
            token=self.session.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["requires_owner_approval"])
        self.assertFalse(result["memory_written"])
        candidate = result["candidates"][0]
        self.assertTrue(candidate["requires_owner_approval"])

        path = "/v1/memory/candidates/" + candidate["candidate_id"] + "/approve"
        status, approved = self.request(
            "POST", path, {"conversation_id": conversation_id}, token=self.session.token,
        )
        self.assertEqual(status, 201)
        self.assertTrue(approved["memory"]["verified"])
        self.assertEqual(approved["memory"]["lifecycle_status"], "approved")

        status, repeated = self.request(
            "POST", path, {"conversation_id": conversation_id}, token=self.session.token,
        )
        self.assertEqual(status, 201)
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["memory"]["memory_id"], approved["memory"]["memory_id"])

    def test_memory_lifecycle_correction_history_and_delete_are_owner_scoped(self):
        status, created = self.request(
            "POST", "/v1/memory", {"content": "Yuki should remember this test note."},
            token=self.session.token,
        )
        self.assertEqual(status, 201)
        memory_id = created["memory"]["memory_id"]

        status, approved = self.request(
            "PATCH", "/v1/memory/" + memory_id,
            {"status": "approved", "reason": "Confirmed by account owner."},
            token=self.session.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(approved["memory"]["verified"])

        status, corrected = self.request(
            "POST", "/v1/memory/" + memory_id + "/corrections",
            {"content": "Yuki should remember the corrected test note.",
             "source": "owner_memory_panel", "reason": "Owner corrected wording."},
            token=self.session.token,
        )
        self.assertEqual(status, 201)
        replacement_id = corrected["memory"]["memory_id"]
        self.assertNotEqual(replacement_id, memory_id)
        self.assertFalse(corrected["memory"]["verified"])

        status, history = self.request(
            "GET", "/v1/memory/" + replacement_id + "/history",
            token=self.session.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(history["corrections"])

        status, deleted = self.request(
            "DELETE", "/v1/memory/" + replacement_id, token=self.session.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])

    def test_memory_routes_require_authentication(self):
        status, result = self.request("GET", "/v1/memory")
        self.assertEqual(status, 401)
        self.assertEqual(result["error"], "authentication_required")


if __name__ == "__main__":
    unittest.main()
