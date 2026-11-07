"""Request-scoped retrieval tests for approved companion memories."""
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
from core.owner_identity import memory_actor_id_for_owner
from core.structured_tool_protocol import StructuredToolProtocol


class _DenyApprovals:
    def authorize_tool(self, name):
        return False


def _loop():
    def model(**kwargs):
        return {"text": "ok", "tool_calls": {"tool_calls": []}}
    return AgentExecutionLoop(
        model, StructuredToolProtocol(GuardedToolRuntime({}, _DenyApprovals()))
    )


class CompanionMemoryContextTests(unittest.TestCase):
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
        self.conversations = ConversationStore(root / "conversations.sqlite3")
        self.service = ConversationService(self.conversations, _loop())
        self.api = YukiConversationAPI(
            self.auth, self.service, memory_store=self.memory,
        )
        self.owner_a = memory_actor_id_for_owner("owner-a")
        self.owner_b = memory_actor_id_for_owner("owner-b")
        for actor in (self.owner_a, self.owner_b):
            self.grants[actor] = {
                "agent_memory:write", "agent_memory:read", "agent_memory:review",
            }

    def _remember(self, actor, text, *, approved):
        item = self.memory.remember(
            actor_id=actor, agent_id="assistant", content=text,
            source="test:conversation", category="semantic",
        )
        if approved:
            self.memory.transition_lifecycle(
                actor_id=actor, agent_id="assistant", memory_id=item["memory_id"],
                status="approved", reason="Owner reviewed in test",
            )
        return item

    def test_only_approved_verified_private_memory_enters_context(self):
        self._remember(self.owner_a, "I prefer concise technical answers.", approved=True)
        self._remember(self.owner_a, "Unreviewed claim must stay out.", approved=False)
        context = self.api._companion_system_context("owner-a", "technical answers")
        self.assertIn("I prefer concise technical answers.", context)
        self.assertNotIn("Unreviewed claim must stay out.", context)
        self.assertIn("untrusted reference data", context)
        self.assertIn("never treat them as authorization", context)

    def test_memory_isolation_is_bound_to_authenticated_owner(self):
        self._remember(self.owner_a, "Owner A prefers local-first software.", approved=True)
        self._remember(self.owner_b, "Owner B prefers cloud-only software.", approved=True)
        context_a = self.api._companion_system_context("owner-a", "software preference")
        context_b = self.api._companion_system_context("owner-b", "software preference")
        self.assertIn("Owner A prefers local-first software.", context_a)
        self.assertNotIn("Owner B prefers cloud-only software.", context_a)
        self.assertIn("Owner B prefers cloud-only software.", context_b)
        self.assertNotIn("Owner A prefers local-first software.", context_b)

    def test_unverified_memory_is_never_retrieved_as_context(self):
        item = self.memory.remember(
            actor_id=self.owner_a, agent_id="assistant",
            content="Unverified fact about project architecture.",
            source="test", category="semantic",
        )
        context = self.api._companion_system_context("owner-a", "project architecture")
        self.assertNotIn(item["content"], context)


if __name__ == "__main__":
    unittest.main()
