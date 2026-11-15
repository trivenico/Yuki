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
        with self.assertRaises(ToolActionTransitionError):
            self.journal.begin_execution("action-1", record.request_digest)

    def test_success_is_durable_and_same_key_never_dispatches_again(self):
        record, _ = self.reserve_and_start()
        succeeded = self.journal.mark_succeeded(record.idempotency_key, {"provider_id": "m-1"})
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
        unknown = self.journal.mark_outcome_unknown(record.idempotency_key)
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
        failed = self.journal.mark_not_executed(record.idempotency_key, "connection_not_opened")
        self.assertEqual(failed.state, "not_executed")
        self.assertEqual(failed.error_code, "connection_not_opened")
        with self.assertRaises(ToolActionTransitionError):
            self.journal.mark_succeeded(record.idempotency_key, {"ok": True})

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
