import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.tool_action_journal import (
    ToolActionConflict,
    ToolActionJournal,
    ToolActionTransitionError,
)


class ToolActionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = str(Path(self.temp_dir.name) / "actions.sqlite3")
        self.journal = ToolActionJournal(self.path)

    def reserve_and_start(self, key="email:message-123", request=None):
        request = request or {"to": "person@example.com", "subject": "Hello"}
        record = self.journal.reserve(key, "email.send", request)
        started = self.journal.begin_execution(key, record.request_digest)
        return record, started

    def test_reserve_is_idempotent_for_same_key_and_payload(self):
        first = self.journal.reserve("action-1", "calendar.create", {"title": "Review"})
        second = self.journal.reserve("action-1", "calendar.create", {"title": "Review"})
        self.assertEqual(first.action_id, second.action_id)
        self.assertEqual(second.state, "prepared")
        self.assertEqual(second.version, 0)

    def test_same_key_cannot_be_reused_for_different_tool_or_payload(self):
        self.journal.reserve("action-1", "calendar.create", {"title": "Review"})
        with self.assertRaises(ToolActionConflict):
            self.journal.reserve("action-1", "calendar.delete", {"title": "Review"})
        with self.assertRaises(ToolActionConflict):
            self.journal.reserve("action-1", "calendar.create", {"title": "Different"})

    def test_execution_claim_is_single_use(self):
        record = self.journal.reserve("action-1", "email.send", {"to": "a@example.com"})
        started = self.journal.begin_execution("action-1", record.request_digest)
        self.assertEqual(started.state, "executing")
        self.assertEqual(started.version, 1)
        self.assertTrue(started.lease_owner)
        self.assertTrue(started.lease_expires_at)
        with self.assertRaises(ToolActionTransitionError):
            self.journal.begin_execution("action-1", record.request_digest)

    def test_success_is_durable_and_same_key_never_dispatches_again(self):
        record, _ = self.reserve_and_start()
        succeeded = self.journal.mark_succeeded(record.idempotency_key, self.journal.get(record.idempotency_key).lease_owner, {"provider_id": "m-1"})
        self.assertEqual(succeeded.state, "succeeded")
        self.assertEqual(succeeded.result, {"provider_id": "m-1"})
        reopened = ToolActionJournal(self.path)
        loaded = reopened.get(record.idempotency_key)
        self.assertEqual(loaded.state, "succeeded")
        self.assertEqual(loaded.result, {"provider_id": "m-1"})
        self.assertEqual(
            reopened.begin_execution(record.idempotency_key, record.request_digest).state,
            "succeeded",
        )

    def test_uncertain_outcome_blocks_automatic_replay_until_reconciled(self):
        record, _ = self.reserve_and_start()
        unknown = self.journal.mark_outcome_unknown(record.idempotency_key, self.journal.get(record.idempotency_key).lease_owner)
        self.assertEqual(unknown.state, "outcome_unknown")
        with self.assertRaises(ToolActionTransitionError):
            self.journal.begin_execution(record.idempotency_key, record.request_digest)
        resolved = self.journal.reconcile_not_executed(record.idempotency_key, "provider_confirms_absent")
        self.assertEqual(resolved.state, "not_executed")
        with self.assertRaises(ToolActionTransitionError):
            self.journal.begin_execution(record.idempotency_key, record.request_digest)

    def test_uncertain_outcome_can_be_reconciled_as_success(self):
        record, _ = self.reserve_and_start()
        self.journal.mark_outcome_unknown(record.idempotency_key)
        resolved = self.journal.reconcile_succeeded(
            record.idempotency_key, {"provider_id": "accepted-9"},
        )
        self.assertEqual(resolved.state, "succeeded")
        self.assertEqual(resolved.result, {"provider_id": "accepted-9"})

    def test_proven_pre_dispatch_failure_is_terminal_not_executed(self):
        record, _ = self.reserve_and_start()
        failed = self.journal.mark_not_executed(record.idempotency_key, self.journal.get(record.idempotency_key).lease_owner, "connection_not_opened")
        self.assertEqual(failed.state, "not_executed")
        self.assertEqual(failed.error_code, "connection_not_opened")
        with self.assertRaises(ToolActionTransitionError):
            self.journal.mark_succeeded(record.idempotency_key, "stale-worker", {"ok": True})

    def test_execution_lease_renews_and_fences_stale_owner(self):
        record = self.journal.reserve("lease-1", "email.send", {"to": "a@example.com"})
        started = self.journal.begin_execution("lease-1", record.request_digest)
        renewed = self.journal.renew_execution("lease-1", started.lease_owner, lease_seconds=60)
        self.assertEqual(renewed.state, "executing")
        self.assertEqual(renewed.lease_owner, started.lease_owner)
        self.assertGreaterEqual(renewed.version, started.version + 1)
        with self.assertRaises(ToolActionTransitionError):
            self.journal.mark_succeeded("lease-1", "wrong-owner", {"ok": True})
        done = self.journal.mark_succeeded("lease-1", renewed.lease_owner, {"ok": True})
        self.assertEqual(done.state, "succeeded")
        self.assertIsNone(done.lease_owner)
        self.assertIsNone(done.lease_expires_at)

    def test_expired_execution_is_recovered_as_unknown_and_stale_worker_is_fenced(self):
        record = self.journal.reserve("lease-expired", "email.send", {"to": "a@example.com"})
        started = self.journal.begin_execution("lease-expired", record.request_digest)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", "lease-expired"),
            )
        self.assertEqual(self.journal.recover_expired_executions(), 1)
        recovered = self.journal.get("lease-expired")
        self.assertEqual(recovered.state, "outcome_unknown")
        self.assertEqual(recovered.error_code, "execution_lease_expired")
        self.assertIsNone(recovered.lease_owner)
        self.assertEqual(self.journal.recover_expired_executions(), 0)
        with self.assertRaises(ToolActionTransitionError):
            self.journal.mark_succeeded("lease-expired", started.lease_owner, {"ok": True})
        with self.assertRaises(ToolActionTransitionError):
            self.journal.renew_execution("lease-expired", started.lease_owner)

    def test_execution_lease_bounds_are_validated(self):
        record = self.journal.reserve("lease-bounds", "email.send", {})
        for invalid in (True, 0, 4, 3601, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.journal.begin_execution("lease-bounds", record.request_digest, lease_seconds=invalid)

    def test_invalid_identifiers_and_oversized_data_are_rejected(self):
        with self.assertRaises(ValueError):
            self.journal.reserve("../bad", "email.send", {})
        with self.assertRaises(ValueError):
            self.journal.reserve("valid", "../send", {})
        with self.assertRaises(ValueError):
            self.journal.reserve("valid", "email.send", {"payload": "x" * 33000})
        with self.assertRaises(ValueError):
            self.journal.mark_succeeded("missing", {"payload": "x" * 17000})


if __name__ == "__main__":
    unittest.main()
