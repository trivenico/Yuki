import tempfile
import unittest
from pathlib import Path

from core.agent_memory import AgentMemoryStore
from evaluation.agent_evaluator import AgentEvaluator
from evaluation.agent_review_queue import AgentEvaluationReviewQueue
from evaluation.approved_knowledge_promoter import ApprovedKnowledgePromoter


class ApprovedKnowledgePromoterTests(unittest.TestCase):
    CONTENT = "The verified deployment procedure requires a staged rollout."
    SOURCE = "verified deployment test"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "yuki.sqlite3")
        permissions = {
            "agent_memory:write", "agent_memory:share", "agent_memory:read",
            "agent_evaluation:enqueue", "agent_evaluation:read",
            "agent_evaluation:review", "agent_knowledge:review",
            "agent_knowledge:revoke", "agent_knowledge:read",
            "agent_memory:delete",
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

    def approve_evaluation(self):
        return self.queue.decide(
            reviewer_actor_id="owner", evaluation_id=self.report.evaluation_id,
            decision="approved", note="Evaluation evidence reviewed.",
        )

    def approve_candidate(self, *, content=None, source=None, scope="private"):
        return self.promoter.review_candidate(
            actor_id="owner", evaluation_id=self.report.evaluation_id,
            agent_id="research", content=content or self.CONTENT,
            source=source or self.SOURCE, decision="approved",
            scope=scope, note="Exact knowledge candidate reviewed.",
        )

    def promote(self, **overrides):
        args = {
            "actor_id": "owner", "evaluation_id": self.report.evaluation_id,
            "agent_id": "research", "content": self.CONTENT,
            "source": self.SOURCE,
        }
        args.update(overrides)
        return self.promoter.promote(**args)

    def approve_both(self):
        self.approve_evaluation()
        self.approve_candidate()

    def test_pending_evaluation_cannot_be_promoted(self):
        with self.assertRaises(PermissionError):
            self.promote()
        self.assertEqual(self.memory.list_memories(
            actor_id="owner", agent_id="research"), [])

    def test_approved_evaluation_without_candidate_approval_cannot_promote(self):
        self.approve_evaluation()
        with self.assertRaisesRegex(PermissionError, "exact_knowledge_candidate"):
            self.promote()

    def test_owner_approval_promotes_exact_candidate_with_provenance(self):
        self.approve_both()
        result = self.promote()
        self.assertEqual(result["status"], "promoted")
        self.assertTrue(result["memory_written"])
        records = self.memory.list_memories(actor_id="owner", agent_id="research")
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["verified"])
        self.assertIn(self.report.evaluation_id, records[0]["source"])
        self.assertEqual(records[0]["scope"], "private")

    def test_candidate_substitution_invalidates_approval(self):
        self.approve_both()
        with self.assertRaisesRegex(PermissionError, "exact_knowledge_candidate"):
            self.promote(content="A substituted unreviewed instruction.")

    def test_repeated_promotion_is_idempotent(self):
        self.approve_both()
        first = self.promote()
        second = self.promote()
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(len(self.memory.list_memories(
            actor_id="owner", agent_id="research")), 1)

    def test_duplicate_candidate_from_another_approved_review_is_not_written_twice(self):
        self.approve_both()
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
        self.promoter.review_candidate(
            actor_id="owner", evaluation_id=second_report.evaluation_id,
            agent_id="research", content=self.CONTENT, source=self.SOURCE,
            decision="approved",
        )
        second = self.promote(evaluation_id=second_report.evaluation_id)
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertEqual(len(self.memory.list_memories(
            actor_id="owner", agent_id="research")), 1)

    def test_agent_mismatch_is_rejected(self):
        self.approve_both()
        with self.assertRaisesRegex(ValueError, "agent_mismatch"):
            self.promote(agent_id="coding")

    def test_shared_candidate_requires_share_permission(self):
        self.approve_evaluation()
        permissions = {"agent_memory:write", "agent_evaluation:enqueue",
                       "agent_evaluation:read", "agent_evaluation:review",
                       "agent_knowledge:review"}
        memory = AgentMemoryStore(
            self.db, authorize=lambda actor, action, _agent:
            actor == "owner" and action in permissions)
        promoter = ApprovedKnowledgePromoter(memory, self.queue)
        with self.assertRaises(PermissionError):
            promoter.review_candidate(
                actor_id="owner", evaluation_id=self.report.evaluation_id,
                agent_id="research", content="A verified shared fact.",
                source="test", decision="approved", scope="shared",
            )

    def test_rejected_evaluation_cannot_be_promoted(self):
        self.queue.decide(reviewer_actor_id="owner",
                          evaluation_id=self.report.evaluation_id, decision="rejected")
        with self.assertRaises(PermissionError):
            self.approve_candidate()


    def test_revocation_removes_memory_and_preserves_audit_record(self):
        self.approve_both()
        promoted = self.promote()
        revoked = self.promoter.revoke(
            actor_id="owner", evaluation_id=self.report.evaluation_id,
            candidate_sha256=promoted["candidate_sha256"],
            reason="Evidence was superseded by a corrected source.",
        )
        self.assertTrue(revoked["memory_removed"])
        self.assertEqual(self.memory.list_memories(
            actor_id="owner", agent_id="research"), [])
        audit = self.promoter.get_revocation(
            actor_id="owner", evaluation_id=self.report.evaluation_id,
            candidate_sha256=promoted["candidate_sha256"],
        )
        self.assertEqual(audit["revoked_by"], "owner")
        self.assertTrue(audit["memory_removed"])
        retry = self.promoter.revoke(
            actor_id="owner", evaluation_id=self.report.evaluation_id,
            candidate_sha256=promoted["candidate_sha256"], reason="Retry",
        )
        self.assertTrue(retry["idempotent"])

    def test_duplicate_promotion_cannot_revoke_another_memory(self):
        self.approve_both()
        self.memory.remember(
            actor_id="owner", agent_id="research", content=self.CONTENT,
            source="existing memory", category="semantic", scope="private",
            verified=True,
        )
        duplicate = self.promote()
        self.assertEqual(duplicate["status"], "duplicate")
        with self.assertRaisesRegex(ValueError, "does_not_own_memory"):
            self.promoter.revoke(
                actor_id="owner", evaluation_id=self.report.evaluation_id,
                candidate_sha256=duplicate["candidate_sha256"],
                reason="Must not delete unrelated memory.",
            )
        self.assertEqual(len(self.memory.list_memories(
            actor_id="owner", agent_id="research")), 1)

    def test_revocation_requires_explicit_permission(self):
        self.approve_both()
        promoted = self.promote()
        restricted = AgentEvaluationReviewQueue(
            self.db,
            authorize=lambda actor, action, _agent:
            actor == "owner" and action != "agent_knowledge:revoke",
        )
        promoter = ApprovedKnowledgePromoter(self.memory, restricted)
        with self.assertRaises(PermissionError):
            promoter.revoke(
                actor_id="owner", evaluation_id=self.report.evaluation_id,
                candidate_sha256=promoted["candidate_sha256"], reason="Unauthorized",
            )


if __name__ == "__main__":
    unittest.main()
