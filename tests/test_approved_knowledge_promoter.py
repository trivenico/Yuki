import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from evaluation.agent_evaluator import AgentEvaluator
from evaluation.agent_review_queue import AgentEvaluationReviewQueue
from evaluation.approved_knowledge_promoter import ApprovedKnowledgePromoter


class ApprovedKnowledgePromoterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "yuki.sqlite3")
        permissions = {
            "agent_memory:write", "agent_memory:share", "agent_memory:read",
            "agent_evaluation:enqueue", "agent_evaluation:read",
            "agent_evaluation:review",
        }
        authorize = lambda actor, action, _agent: actor == "owner" and action in permissions
        self.memory = AgentMemoryStore(self.db, authorize=authorize)
        self.queue = AgentEvaluationReviewQueue(self.db, authorize=authorize)
        self.promoter = ApprovedKnowledgePromoter(self.memory, self.queue)
        self.report = AgentEvaluator().evaluate(
            task_id="task-promotion-1", agent_id="research", outcome="succeeded",
            signals={name: 1.0 for name in (
                "correctness", "evidence_quality", "instruction_compliance",
                "completeness", "efficiency",
            )},
            evidence_refs=["test-run:verified", "artifact:verified"],
        )
        self.queue.enqueue(actor_id="owner", report=self.report)

    def approve(self):
        return self.queue.decide(
            reviewer_actor_id="owner", evaluation_id=self.report.evaluation_id,
            decision="approved", note="Verified against independent evidence.",
        )

    def promote(self, **overrides):
        args = {
            "actor_id": "owner", "evaluation_id": self.report.evaluation_id,
            "agent_id": "research",
            "content": "The verified deployment procedure requires a staged rollout.",
            "source": "verified deployment test",
        }
        args.update(overrides)
        return self.promoter.promote(**args)

    def test_pending_review_cannot_be_promoted(self):
        with self.assertRaises(PermissionError):
            self.promote()
        self.assertEqual(self.memory.list_memories(
            actor_id="owner", agent_id="research"), [])

    def test_owner_approval_promotes_with_provenance_and_verified_flag(self):
        self.approve()
        result = self.promote()
        self.assertEqual(result["status"], "promoted")
        self.assertTrue(result["memory_written"])
        records = self.memory.list_memories(actor_id="owner", agent_id="research")
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["verified"])
        self.assertIn(self.report.evaluation_id, records[0]["source"])
        self.assertEqual(records[0]["scope"], "private")

    def test_repeated_promotion_is_idempotent(self):
        self.approve()
        first = self.promote()
        second = self.promote()
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(self.memory.list_memories(
            actor_id="owner", agent_id="research")), 1)

    def test_duplicate_candidate_from_another_approved_review_is_not_written_twice(self):
        self.approve()
        first = self.promote()
        second_report = AgentEvaluator().evaluate(
            task_id="task-promotion-2", agent_id="research", outcome="succeeded",
            signals={name: 1.0 for name in (
                "correctness", "evidence_quality", "instruction_compliance",
                "completeness", "efficiency",
            )},
            evidence_refs=["test-run:verified-2", "artifact:verified-2"],
        )
        self.queue.enqueue(actor_id="owner", report=second_report)
        self.queue.decide(reviewer_actor_id="owner",
                          evaluation_id=second_report.evaluation_id, decision="approved")
        second = self.promote(evaluation_id=second_report.evaluation_id)
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertEqual(len(self.memory.list_memories(
            actor_id="owner", agent_id="research")), 1)

    def test_agent_mismatch_is_rejected(self):
        self.approve()
        with self.assertRaisesRegex(ValueError, "agent_mismatch"):
            self.promote(agent_id="coding")

    def test_shared_memory_requires_share_permission(self):
        self.approve()
        permissions = {"agent_memory:write", "agent_evaluation:enqueue",
                       "agent_evaluation:read", "agent_evaluation:review"}
        memory = AgentMemoryStore(
            self.db, authorize=lambda actor, action, _agent:
            actor == "owner" and action in permissions)
        promoter = ApprovedKnowledgePromoter(memory, self.queue)
        with self.assertRaises(PermissionError):
            promoter.promote(
                actor_id="owner", evaluation_id=self.report.evaluation_id,
                agent_id="research", content="A verified shared fact.",
                source="test", scope="shared",
            )

    def test_rejected_review_cannot_be_promoted(self):
        self.queue.decide(reviewer_actor_id="owner",
                          evaluation_id=self.report.evaluation_id, decision="rejected")
        with self.assertRaises(PermissionError):
            self.promote()


if __name__ == "__main__":
    unittest.main()
