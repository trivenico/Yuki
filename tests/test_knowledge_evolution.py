import tempfile
import unittest
from pathlib import Path

from memory.collective_vault import CollectiveMemoryVault
from memory.evolution import EvolutionError, KnowledgeEvolutionEngine


class KnowledgeEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.actors = {"alice", "reviewer"}
        def allowed(actor, action, resource):
            return actor in self.actors and action in {
                "memory:write", "memory:update", "memory:read",
                "evolution:propose", "evolution:read", "evolution:review",
            }
        self.vault = CollectiveMemoryVault(
            str(Path(self.tmp.name) / "vault.sqlite3"), authorize=allowed
        )
        self.engine = KnowledgeEvolutionEngine(
            self.vault, authorize=allowed, require_independent_reviewer=True
        )
        self.record = self.vault.remember(
            actor_id="alice", scope="project", scope_id="yuki",
            category="decision", content="Use SQLite for project memory.",
            source_type="decision", source_ref="ADR-001",
        )

    def proposal(self):
        return self.engine.propose(
            actor_id="alice", memory_id=self.record.memory_id,
            proposed_content="Use SQLite with encrypted backups for project memory.",
            rationale="Backup review identified a missing recovery requirement.",
            evidence_ref="ADR-002",
        )

    def test_approval_updates_memory_and_versions(self):
        proposal = self.proposal()
        with self.assertRaises(PermissionError):
            self.engine.review(reviewer_id="alice", proposal_id=proposal.proposal_id, decision="approve")
        result = self.engine.review(
            reviewer_id="reviewer", proposal_id=proposal.proposal_id, decision="approve"
        )
        self.assertEqual(result.status, "approved")
        updated = self.vault.get(actor_id="reviewer", memory_id=self.record.memory_id)
        self.assertEqual(updated.version, 2)
        self.assertIn("encrypted backups", updated.content)

    def test_rejection_does_not_change_memory(self):
        proposal = self.proposal()
        result = self.engine.review(
            reviewer_id="reviewer", proposal_id=proposal.proposal_id, decision="reject"
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(self.vault.get(actor_id="alice", memory_id=self.record.memory_id).version, 1)

    def test_stale_proposal_is_rejected(self):
        proposal = self.proposal()
        self.vault.remember(
            actor_id="alice", scope="project", scope_id="yuki",
            category="decision", content="A newer decision already exists.",
            source_type="decision", source_ref="ADR-003",
            memory_id=self.record.memory_id,
        )
        with self.assertRaises(EvolutionError):
            self.engine.review(
                reviewer_id="reviewer", proposal_id=proposal.proposal_id, decision="approve"
            )
        self.assertEqual(
            self.vault.get(actor_id="alice", memory_id=self.record.memory_id).version, 2
        )
        self.assertEqual(
            self.engine.get(actor_id="alice", proposal_id=proposal.proposal_id).status,
            "pending",
        )

    def test_proposal_requires_change_and_authorized_actor(self):
        with self.assertRaises(EvolutionError):
            self.engine.propose(
                actor_id="alice", memory_id=self.record.memory_id,
                proposed_content=self.record.content, rationale="same",
                evidence_ref="ADR-001",
            )
        with self.assertRaises(PermissionError):
            self.engine.propose(
                actor_id="mallory", memory_id=self.record.memory_id,
                proposed_content="Injected content.", rationale="bad actor",
                evidence_ref="unknown",
            )

    def test_review_is_single_use(self):
        proposal = self.proposal()
        self.engine.review(reviewer_id="reviewer", proposal_id=proposal.proposal_id, decision="reject")
        with self.assertRaises(EvolutionError):
            self.engine.review(reviewer_id="reviewer", proposal_id=proposal.proposal_id, decision="approve")


if __name__ == "__main__":
    unittest.main()
