import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation.host_reviewer_signing import HostReviewerSigningKeyRegistry
from automation.reviewer_ledger import ReviewerTaskLedger
from automation.reviewer_orchestrator import ReviewerOrchestrator, ReviewerOrchestrationError
from automation.reviewer_sessions import ReviewerSessionAuthority
from automation.reviewer_signing import HMACReviewerSigner


class ReviewerOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.digest = "d" * 64
        self.allowed = True
        def allowed(actor, action, digest):
            return self.allowed and digest == self.digest and action in {
                "review:assign", "review:execute", "review:submit", "review:read",
            }
        self.sessions = ReviewerSessionAuthority(
            root / "sessions.sqlite", secret=b"s" * 32,
            authorize=lambda actor, action, digest: (
                self.allowed and digest == self.digest
                and action in {"review:execute", "review:submit"}
            ),
        )
        self.keys = HostReviewerSigningKeyRegistry(
            authorize=lambda actor, action: self.allowed and action in {
                "review:sign", "review:verify_signature",
            }
        )
        for agent_id, key_id, key in (
            ("reviewer-a", "key-a", b"a" * 32),
            ("reviewer-b", "key-b", b"b" * 32),
        ):
            self.keys.register(
                reviewer_agent_id=agent_id,
                signer=HMACReviewerSigner(key_id=key_id, key=key),
                active_for_signing=True,
            )
        self.ledger = ReviewerTaskLedger(
            root / "ledger.sqlite", authorize=allowed,
            signer_resolver=self.keys.resolve, session_authority=self.sessions,
        )
        self.orchestrator = ReviewerOrchestrator(
            self.ledger, self.sessions,
            authorize_coordinator=lambda actor, action, digest: (
                self.allowed and actor == "coordinator" and action == "review:orchestrate"
                and digest == self.digest
            ),
        )
        self.agents = (
            SimpleNamespace(agent_id="reviewer-a", capabilities=frozenset({"review_requirements"}), enabled=True),
            SimpleNamespace(agent_id="reviewer-b", capabilities=frozenset({"review_testing"}), enabled=True),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_provisions_durable_tasks_and_artifact_scoped_sessions(self):
        batch = self.orchestrator.provision_batch(
            coordinator_id="coordinator", artifact_sha256=self.digest,
            author_agent_id="author", agents=self.agents,
            requirements_by_check={
                "acceptance_criteria": ("Check acceptance criteria.",),
                "tests": ("Inspect test evidence.",),
            },
            evidence_references_by_check={
                "acceptance_criteria": (), "tests": ("artifact://tests",),
            },
        )
        self.assertEqual(len(batch.tasks), 2)
        for task in batch.tasks:
            record = self.ledger.get(actor_id="coordinator", task_id=task.task_id)
            self.assertEqual(record["state"], "assigned")
            self.assertEqual(record["reviewer_agent_id"], task.reviewer_agent_id)
            self.assertTrue(self.sessions.validate(
                task.session_token, reviewer_agent_id=task.reviewer_agent_id,
                artifact_sha256=self.digest, action="review:execute",
                operation_id="validate-" + task.task_id,
            ))

    def test_denied_coordinator_does_not_create_tasks(self):
        self.allowed = False
        with self.assertRaisesRegex(ReviewerOrchestrationError, "not_authorized"):
            self.orchestrator.provision_batch(
                coordinator_id="coordinator", artifact_sha256=self.digest,
                author_agent_id="author", agents=self.agents,
                requirements_by_check={
                    "acceptance_criteria": ("Check acceptance criteria.",),
                    "tests": ("Inspect test evidence.",),
                },
                evidence_references_by_check={},
            )

    def test_missing_reviewer_session_key_policy_fails_before_task_creation(self):
        self.allowed = False
        with self.assertRaises(ReviewerOrchestrationError):
            self.orchestrator.provision_batch(
                coordinator_id="coordinator", artifact_sha256=self.digest,
                author_agent_id="author", agents=self.agents,
                requirements_by_check={
                    "acceptance_criteria": ("Check acceptance criteria.",),
                    "tests": ("Inspect test evidence.",),
                },
                evidence_references_by_check={},
            )


if __name__ == "__main__":
    unittest.main()
