import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation.host_reviewer_signing import HostReviewerSigningKeyRegistry
from automation.reviewer_ledger import ReviewerTaskLedger, ReviewerLedgerConflict
from automation.reviewer_leases import ReviewerLeaseAuthority
from automation.reviewer_team import ReviewerAssignment, ReviewerTeamPlan
from automation.reviewer_workflow import build_review_tasks
import sqlite3
from datetime import datetime, timedelta, timezone
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
                "review:lease:acquire", "review:lease:recover",
                "review:dispatch:claim", "review:dispatch:complete",
                "review:dispatch:fail", "review:dispatch:read",
                "review:dispatch:recover",
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
        ledger_path = root / "ledger.sqlite"
        self.leases = ReviewerLeaseAuthority(
            ledger_path, secret=b"l" * 32,
            authorize=lambda actor, action, digest: (
                self.allowed and actor == "coordinator" and digest == self.digest
                and action in {"review:lease:acquire", "review:lease:recover"}
            ),
        )
        self.ledger = ReviewerTaskLedger(
            ledger_path, authorize=allowed,
            signer_resolver=self.keys.resolve, session_authority=self.sessions,
            lease_authority=self.leases,
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
            self.assertIsInstance(task.lease_token, str)
            self.assertGreaterEqual(task.lease_generation, 1)
            self.assertTrue(self.leases.validate(
                task.lease_token, task_id=task.task_id,
                artifact_sha256=self.digest, reviewer_agent_id=task.reviewer_agent_id,
                generation=task.lease_generation,
            ))
            self.assertTrue(self.sessions.validate(
                task.session_token, reviewer_agent_id=task.reviewer_agent_id,
                artifact_sha256=self.digest, action="review:execute",
                operation_id="validate-" + task.task_id,
            ))

    def test_recovery_discovers_expired_work_and_provisions_replacement(self):
        ledger_path = Path(self.temp.name) / "recover-ledger.sqlite"
        session_path = Path(self.temp.name) / "recover-sessions.sqlite"
        lease_auth = lambda actor, action, digest: (
            self.allowed and actor == "coordinator" and digest == self.digest
            and action in {"review:lease:acquire", "review:lease:recover"}
        )
        leases = ReviewerLeaseAuthority(ledger_path, secret=b"l" * 32, authorize=lease_auth)
        sessions = ReviewerSessionAuthority(
            session_path, secret=b"r" * 32,
            authorize=lambda reviewer, action, digest: (
                self.allowed and digest == self.digest
                and action in {"review:execute", "review:submit"}
            ),
        )
        permissions = {
            "review:assign", "review:execute", "review:submit", "review:read",
            "review:recover:list", "review:abandon", "review:reassign",
        }
        ledger = ReviewerTaskLedger(
            ledger_path,
            authorize=lambda actor, action, digest: (
                self.allowed and digest == self.digest and
                ((actor == "coordinator" and action in permissions) or
                 (actor in {"reviewer-a", "reviewer-b"} and action in {"review:execute", "review:submit"}))
            ),
            signer_resolver=self.keys.resolve, session_authority=sessions,
            lease_authority=leases,
        )
        plan = ReviewerTeamPlan(
            artifact_sha256=self.digest, author_agent_id="author",
            assignments=(ReviewerAssignment("tests", "reviewer-a"),),
        )
        task = build_review_tasks(
            plan, requirements_by_check={"tests": ("Inspect tests.",)},
            evidence_references_by_check={"tests": ("artifact://tests",)},
        )[0]
        ledger.create_tasks(actor_id="coordinator", tasks=(task,))
        session = sessions.issue(reviewer_agent_id="reviewer-a", artifact_sha256=self.digest)
        lease = leases.acquire(
            actor_id="coordinator", task_id=task.task_id,
            artifact_sha256=self.digest, reviewer_agent_id="reviewer-a",
        )
        ledger.claim(
            actor_id="reviewer-a", task_id=task.task_id, expected_version=0,
            session_token=session, operation_id="recover-claim",
            lease_token=lease.token, lease_generation=lease.generation,
        )
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sqlite3.connect(ledger_path) as db:
            db.execute("UPDATE reviewer_leases SET expires_at=? WHERE task_id=?", (expired, task.task_id))
        orchestrator = ReviewerOrchestrator(
            ledger, sessions,
            authorize_coordinator=lambda actor, action, digest: (
                actor == "coordinator" and action == "review:orchestrate" and digest == self.digest
            ),
        )
        agents = (
            SimpleNamespace(agent_id="reviewer-a", capabilities=frozenset({"review_testing"}), enabled=True),
            SimpleNamespace(agent_id="reviewer-b", capabilities=frozenset({"review_testing"}), enabled=True),
        )
        outcomes = orchestrator.recover_expired_tasks(
            coordinator_id="coordinator", agents=agents,
        )
        self.assertEqual(len(outcomes), 1)
        outcome = outcomes[0]
        self.assertEqual(outcome.state, "recovered")
        self.assertEqual(outcome.replacement_reviewer_agent_id, "reviewer-b")
        self.assertEqual(outcome.provisioned_task.lease_generation, 2)
        self.assertEqual(outcome.provisioned_task.review_task.reviewer_agent_id, "reviewer-b")
        self.assertTrue(sessions.validate(
            outcome.provisioned_task.session_token,
            reviewer_agent_id="reviewer-b", artifact_sha256=self.digest,
            action="review:execute", operation_id="replacement-session-check",
        ))
        record = ledger.get(actor_id="coordinator", task_id=task.task_id)
        self.assertEqual(record["reviewer_agent_id"], "reviewer-b")
        self.assertEqual(record["task"]["reviewer_agent_id"], "reviewer-b")
        self.assertEqual(record["state"], "assigned")

    def test_initial_assignment_creates_dispatch_and_worker_can_complete_claim(self):
        batch=self.orchestrator.provision_batch(
            coordinator_id="coordinator",artifact_sha256=self.digest,
            author_agent_id="author",agents=(self.agents[0],),
            requirements_by_check={"acceptance_criteria":("Check criteria.",)},
            evidence_references_by_check={"acceptance_criteria":()},
        )
        task=batch.tasks[0]
        self.assertIsInstance(task.dispatch_id,str)
        records=self.ledger.list_dispatches(
            actor_id="coordinator",artifact_sha256=self.digest,
        )
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]["state"],"pending")
        claimed=self.ledger.claim_dispatches(
            actor_id="reviewer-a",worker_id="reviewer-a",limit=5,
        )
        self.assertEqual(len(claimed),1)
        self.assertEqual(claimed[0]["task_id"],task.task_id)
        self.assertEqual(claimed[0]["task"]["reviewer_agent_id"],"reviewer-a")
        receipt=self.ledger.complete_dispatch(
            actor_id="reviewer-a",dispatch_id=claimed[0]["dispatch_id"],
            claim_token=claimed[0]["claim_token"],
        )
        self.assertEqual(receipt["state"],"delivered")
        self.assertEqual(self.ledger.list_dispatches(
            actor_id="coordinator",artifact_sha256=self.digest,
        )[0]["state"],"delivered")

    def test_dispatch_claim_cannot_impersonate_assigned_worker(self):
        self.orchestrator.provision_batch(
            coordinator_id="coordinator",artifact_sha256=self.digest,
            author_agent_id="author",agents=(self.agents[0],),
            requirements_by_check={"acceptance_criteria":("Check criteria.",)},
            evidence_references_by_check={"acceptance_criteria":()},
        )
        with self.assertRaisesRegex(PermissionError,"cannot_impersonate"):
            self.ledger.claim_dispatches(
                actor_id="coordinator",worker_id="reviewer-a",
            )

    def test_dispatch_failure_uses_bounded_retry_then_dead_letters(self):
        self.orchestrator.provision_batch(
            coordinator_id="coordinator",artifact_sha256=self.digest,
            author_agent_id="author",agents=(self.agents[0],),
            requirements_by_check={"acceptance_criteria":("Check criteria.",)},
            evidence_references_by_check={"acceptance_criteria":()},
        )
        claimed=self.ledger.claim_dispatches(
            actor_id="reviewer-a",worker_id="reviewer-a",max_attempts=1,
        )[0]
        result=self.ledger.fail_dispatch(
            actor_id="reviewer-a",dispatch_id=claimed["dispatch_id"],
            claim_token=claimed["claim_token"],error_code="worker.unavailable",
            max_attempts=1,base_backoff_seconds=1,
        )
        self.assertEqual(result["state"],"dead_letter")
        self.assertEqual(result["attempts"],1)
        self.assertEqual(self.ledger.list_dispatches(
            actor_id="coordinator",artifact_sha256=self.digest,
        )[0]["last_error"],"worker.unavailable")

    def test_expired_dispatch_claim_is_reclaimed_without_reusing_token(self):
        self.orchestrator.provision_batch(
            coordinator_id="coordinator",artifact_sha256=self.digest,
            author_agent_id="author",agents=(self.agents[0],),
            requirements_by_check={"acceptance_criteria":("Check criteria.",)},
            evidence_references_by_check={"acceptance_criteria":()},
        )
        first=self.ledger.claim_dispatches(
            actor_id="reviewer-a",worker_id="reviewer-a",
        )[0]
        with sqlite3.connect(Path(self.temp.name) / "ledger.sqlite") as db:
            db.execute("UPDATE reviewer_dispatch_outbox SET claim_expires_at=? WHERE dispatch_id=?",
                       ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
                        first["dispatch_id"]))
        second=self.ledger.claim_dispatches(
            actor_id="reviewer-a",worker_id="reviewer-a",
        )[0]
        self.assertNotEqual(first["claim_token"],second["claim_token"])
        with self.assertRaises(ReviewerLedgerConflict):
            self.ledger.complete_dispatch(
                actor_id="reviewer-a",dispatch_id=first["dispatch_id"],
                claim_token=first["claim_token"],
            )

    def test_initial_lease_failure_rolls_back_task_assignment(self):
        self.leases._authorize = lambda actor, action, digest: False
        with self.assertRaisesRegex(ReviewerOrchestrationError, "persistence_failed"):
            self.orchestrator.provision_batch(
                coordinator_id="coordinator", artifact_sha256=self.digest,
                author_agent_id="author", agents=(self.agents[0],),
                requirements_by_check={"acceptance_criteria": ("Check criteria.",)},
                evidence_references_by_check={"acceptance_criteria": ()},
            )
        with sqlite3.connect(Path(self.temp.name) / "ledger.sqlite") as db:
            count = db.execute("SELECT COUNT(*) FROM reviewer_tasks").fetchone()[0]
            lease_count = db.execute("SELECT COUNT(*) FROM reviewer_leases").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(lease_count, 0)

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
