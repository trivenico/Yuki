import hashlib
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.upgrade_execution_contracts import ExecutionEvidence, ExecutionRequest
from automation.upgrade_execution_store import (
    UpgradeExecutionConflict,
    UpgradeExecutionStore,
    UpgradeExecutionTransitionError,
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class UpgradeExecutionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = UpgradeExecutionStore(Path(self.temp.name) / "upgrade.sqlite3")
        self.request = ExecutionRequest.create(
            proposal_id="proposal-1",
            snapshot_digest=digest("snapshot"),
            snapshot_object_digest=digest("object"),
            snapshot_version="object-version-1",
            test_targets=("tests.test_upgrade",),
            runner_kind="container",
            runner_image_digest=digest("runner"),
            architecture="linux-amd64",
            runtime_version="python-3.11",
            policy_digest=digest("policy"),
            execution_id="execution-1",
            idempotency_key="idem-1",
            requesting_principal="requester-1",
            reviewer="reviewer-1",
            approval_id="approval-1",
        )

    def evidence(self, request=None, **overrides):
        request = request or self.request
        values = dict(
            execution_id=request.execution_id,
            request_digest=request.request_digest,
            proposal_id=request.proposal_id,
            snapshot_digest=request.snapshot_digest,
            test_plan_digest=request.test_plan_digest,
            runner_image_digest=request.runner_image_digest,
            policy_digest=request.policy_digest,
            outcome="passed",
            exit_code=0,
            started_at="2026-11-06T10:00:00Z",
            finished_at="2026-11-06T10:01:00Z",
            result_digest=digest("result"),
            runner_identity="https://ci.example/workflow/upgrade",
            issuer="https://ci.example",
            audience="yuki-upgrade-verifier",
            nonce="nonce-1",
            key_id="key-1",
            signature="unverified-signature",
        )
        values.update(overrides)
        return ExecutionEvidence(**values)

    def test_create_is_idempotent_and_persists_exact_request(self):
        first = self.store.create(self.request)
        second = self.store.create(self.request)
        self.assertEqual(first.execution_id, second.execution_id)
        self.assertEqual(first.request_digest, self.request.request_digest)
        self.assertEqual(first.state, "queued")
        self.assertEqual(first.version, 0)
        self.assertEqual(len(self.store.list_events(first.execution_id)), 1)

    def test_idempotency_key_cannot_be_reused_for_different_request(self):
        self.store.create(self.request)
        other = ExecutionRequest.create(
            proposal_id="proposal-2",
            snapshot_digest=digest("snapshot-2"),
            snapshot_object_digest=digest("object-2"),
            snapshot_version="object-version-2",
            test_targets=("tests.test_upgrade",),
            runner_kind="container",
            runner_image_digest=digest("runner"),
            architecture="linux-amd64",
            runtime_version="python-3.11",
            policy_digest=digest("policy"),
            execution_id="execution-2",
            idempotency_key="idem-1",
            requesting_principal="requester-1",
            reviewer="reviewer-1",
            approval_id="approval-2",
        )
        with self.assertRaisesRegex(UpgradeExecutionConflict, "idempotency_payload_mismatch"):
            self.store.create(other)

    def test_transitions_use_compare_and_swap_and_reject_invalid_edges(self):
        row = self.store.create(self.request)
        running = self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
        )
        self.assertEqual(running.version, 1)
        with self.assertRaisesRegex(UpgradeExecutionConflict, "stale_execution_version"):
            self.store.transition(
                row.execution_id, expected_version=0, to_state="cancelled",
                actor_id="operator", reason_code="cancel",
            )
        pending = self.store.complete_execution(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            expected_version=running.version,
        )
        self.assertEqual(pending.state, "evidence_pending")
        with self.assertRaises(UpgradeExecutionTransitionError):
            self.store.transition(
                row.execution_id, expected_version=2, to_state="queued",
                actor_id="operator", reason_code="invalid_requeue",
            )

    def test_evidence_is_bound_and_persisted_as_unverified(self):
        row = self.store.create(self.request)
        row = self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
        )
        row = self.store.complete_execution(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            expected_version=row.version,
        )
        stored = self.store.record_evidence(
            row.execution_id, expected_version=row.version,
            evidence=self.evidence(),
        )
        self.assertEqual(stored.state, "evidence_pending")
        self.assertEqual(stored.version, row.version + 1)
        self.assertIn("unverified-signature", stored.evidence_json)
        self.assertEqual(self.store.list_events(row.execution_id)[-1]["reason_code"],
                         "evidence_received_unverified")

    def test_evidence_cannot_be_replayed_or_replaced(self):
        row = self.store.create(self.request)
        row = self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
        )
        row = self.store.complete_execution(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            expected_version=row.version,
        )
        stored = self.store.record_evidence(
            row.execution_id, expected_version=row.version, evidence=self.evidence(),
        )
        with self.assertRaisesRegex(UpgradeExecutionConflict, "evidence_already_recorded"):
            self.store.record_evidence(
                stored.execution_id, expected_version=stored.version,
                evidence=self.evidence(),
            )

    def test_audit_events_are_append_only_at_database_boundary(self):
        row = self.store.create(self.request)
        with closing(self.store._connect()) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "upgrade_event_is_append_only"):
                db.execute(
                    "UPDATE upgrade_execution_events SET reason_code='rewritten' WHERE execution_id=?",
                    (row.execution_id,),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "upgrade_event_is_append_only"):
                db.execute(
                    "DELETE FROM upgrade_execution_events WHERE execution_id=?",
                    (row.execution_id,),
                )
        self.assertEqual(self.store.list_events(row.execution_id)[0]["reason_code"], "admitted")

    def test_request_snapshot_and_execution_records_cannot_be_rewritten_or_deleted(self):
        row = self.store.create(self.request)
        with closing(self.store._connect()) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "upgrade_request_snapshot_is_immutable"):
                db.execute(
                    "UPDATE upgrade_executions SET request_json='{}' WHERE execution_id=?",
                    (row.execution_id,),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "upgrade_execution_is_retained"):
                db.execute(
                    "DELETE FROM upgrade_executions WHERE execution_id=?",
                    (row.execution_id,),
                )
        self.assertEqual(self.store.get(row.execution_id).request_digest, self.request.request_digest)

    def test_recorded_evidence_cannot_be_replaced_at_database_boundary(self):
        row = self.store.create(self.request)
        row = self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
        )
        row = self.store.complete_execution(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            expected_version=row.version,
        )
        stored = self.store.record_evidence(
            row.execution_id, expected_version=row.version, evidence=self.evidence(),
        )
        with closing(self.store._connect()) as db:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "upgrade_evidence_is_write_once"):
                db.execute(
                    "UPDATE upgrade_executions SET evidence_json='{}' WHERE execution_id=?",
                    (stored.execution_id,),
                )
        self.assertEqual(self.store.get(stored.execution_id).evidence_json, stored.evidence_json)

    def test_evidence_for_another_snapshot_is_rejected(self):
        row = self.store.create(self.request)
        row = self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
        )
        row = self.store.complete_execution(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            expected_version=row.version,
        )
        altered = self.evidence(snapshot_digest=digest("wrong snapshot"))
        with self.assertRaisesRegex(UpgradeExecutionConflict, "evidence_request_mismatch"):
            self.store.record_evidence(
                row.execution_id, expected_version=row.version, evidence=altered,
            )



    def test_only_one_worker_can_claim_queued_execution(self):
        from datetime import datetime, timezone
        row = self.store.create(self.request)
        claimed = self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            lease_seconds=30, now=datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(claimed.state, "running")
        self.assertEqual(claimed.version, 1)
        with self.assertRaisesRegex(UpgradeExecutionConflict, "execution_not_queued"):
            self.store.claim_queued(
                row.execution_id, owner_id="worker-b", lease_token="token-b",
                now=datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc),
            )

    def test_lease_renewal_requires_matching_live_owner_and_token(self):
        from datetime import datetime, timezone
        start = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
        row = self.store.create(self.request)
        self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            lease_seconds=30, now=start,
        )
        with self.assertRaisesRegex(UpgradeExecutionConflict, "lease_owner_or_token_mismatch"):
            self.store.renew_lease(
                row.execution_id, owner_id="worker-b", lease_token="token-a", now=start,
            )
        expiry = self.store.renew_lease(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            lease_seconds=60, now=start,
        )
        self.assertEqual(expiry, "2026-11-07T00:01:00+00:00")

    def test_expired_worker_lease_is_rejected_not_automatically_replayed(self):
        from datetime import datetime, timezone
        start = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
        row = self.store.create(self.request)
        self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            lease_seconds=5, now=start,
        )
        with self.assertRaisesRegex(UpgradeExecutionConflict, "lease_not_expired"):
            self.store.reject_expired_lease(
                row.execution_id, now=datetime(2026, 11, 7, 0, 0, 4, tzinfo=timezone.utc),
            )
        recovered = self.store.reject_expired_lease(
            row.execution_id, now=datetime(2026, 11, 7, 0, 0, 6, tzinfo=timezone.utc),
        )
        self.assertEqual(recovered.state, "rejected")
        self.assertEqual(recovered.version, 2)
        self.assertEqual(self.store.list_events(row.execution_id)[-1]["reason_code"], "worker_lease_expired")

    def test_expired_lease_cannot_be_renewed(self):
        from datetime import datetime, timezone
        start = datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
        row = self.store.create(self.request)
        self.store.claim_queued(
            row.execution_id, owner_id="worker-a", lease_token="token-a",
            lease_seconds=5, now=start,
        )
        with self.assertRaisesRegex(UpgradeExecutionConflict, "lease_expired"):
            self.store.renew_lease(
                row.execution_id, owner_id="worker-a", lease_token="token-a",
                now=datetime(2026, 11, 7, 0, 0, 6, tzinfo=timezone.utc),
            )

if __name__ == "__main__":
    unittest.main()
