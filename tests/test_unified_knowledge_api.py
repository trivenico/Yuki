import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from core.agent_memory_retriever import AgentMemoryRetriever
from core.unified_knowledge_api import UnifiedKnowledgeAPI


class UnifiedKnowledgeAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "knowledge.sqlite3")
        self.permissions = {
            "owner": {"agent_memory:write", "agent_memory:read", "agent_memory:share"},
            "intruder": set(),
        }
        self.store = AgentMemoryStore(
            self.db,
            authorize=lambda actor, action, _agent: action in self.permissions.get(actor, set()),
        )
        self.vectors = {
            "deployment": [1.0, 0.0],
            "verified deployment runbook": [0.99, 0.01],
            "unreviewed deployment claim": [0.95, 0.05],
            "other agent private deployment": [0.9, 0.1],
        }
        self.retriever = AgentMemoryRetriever(
            self.store, embed=lambda text: self.vectors[text]
        )
        self.api = UnifiedKnowledgeAPI(self.retriever)

    def add(self, text, *, agent="research", verified=False, scope="private"):
        return self.store.remember(
            actor_id="owner", agent_id=agent, content=text, source="runbook",
            category="semantic", confidence=0.9, verified=verified, scope=scope,
        )

    def test_default_packet_contains_only_verified_knowledge(self):
        approved = self.add("verified deployment runbook", verified=True)
        self.add("unreviewed deployment claim")
        packet = self.api.retrieve(actor_id="owner", agent_id="research", query="deployment")
        self.assertEqual(packet["count"], 1)
        self.assertEqual(packet["evidence"][0]["memory_id"], approved["memory_id"])
        self.assertTrue(packet["policy"]["memory_is_untrusted_data"])
        self.assertTrue(packet["policy"]["retrieval_is_read_only"])

    def test_explicit_candidate_mode_can_include_unverified_records(self):
        self.add("verified deployment runbook", verified=True)
        self.add("unreviewed deployment claim")
        packet = self.api.retrieve(
            actor_id="owner", agent_id="research", query="deployment", verified_only=False
        )
        self.assertEqual(packet["count"], 2)

    def test_agent_scope_is_preserved(self):
        self.add("verified deployment runbook", verified=True)
        self.add("other agent private deployment", agent="developer", verified=True)
        packet = self.api.retrieve(actor_id="owner", agent_id="research", query="deployment")
        self.assertEqual({row["agent_id"] for row in packet["evidence"]}, {"research"})

    def test_shared_knowledge_requires_authorized_shared_read(self):
        self.add("verified deployment runbook", verified=True, scope="shared")
        packet = self.api.retrieve(actor_id="owner", agent_id="research", query="deployment")
        self.assertEqual(packet["count"], 1)
        self.permissions["owner"].remove("agent_memory:share")
        with self.assertRaises(PermissionError):
            self.api.retrieve(actor_id="owner", agent_id="research", query="deployment")

    def test_unauthorized_actor_cannot_read_knowledge(self):
        self.add("verified deployment runbook", verified=True)
        with self.assertRaises(PermissionError):
            self.api.retrieve(actor_id="intruder", agent_id="research", query="deployment")

    def test_invalid_agent_or_actor_is_rejected(self):
        with self.assertRaises(ValueError):
            self.api.retrieve(actor_id="", agent_id="research", query="deployment")
        with self.assertRaises(ValueError):
            self.api.retrieve(actor_id="owner", agent_id="", query="deployment")


if __name__ == "__main__":
    unittest.main()
