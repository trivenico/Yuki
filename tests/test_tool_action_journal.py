import sqlite3
import tempfile
from contextlib import closing
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


    def test_scoped_recovery_fences_only_the_requested_expired_execution(self):
        expired, _ = self.reserve_and_start("action-expired")
        live, _ = self.reserve_and_start("action-live")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", "action-expired"),
            )
            db.commit()

        recovered = self.journal.recover_expired_execution("action-expired")
        self.assertEqual(recovered.state, "outcome_unknown")
        self.assertEqual(recovered.error_code, "execution_lease_expired")
        self.assertEqual(self.journal.get("action-live").state, "executing")

    def test_reserve_in_transaction_requires_and_uses_caller_transaction(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            with self.assertRaisesRegex(RuntimeError, "active caller-owned transaction"):
                self.journal.reserve_in_transaction(
                    db, "atomic-action", "email.send", {"to": "a@example.com"},
                    action_id="a" * 32,
                )
            self.assertIsNone(self.journal.get("atomic-action"))

            db.execute("BEGIN IMMEDIATE")
            reserved = self.journal.reserve_in_transaction(
                db, "atomic-action", "email.send", {"to": "a@example.com"},
                action_id="a" * 32,
            )
            self.assertEqual(reserved.version, 1)
            db.execute("ROLLBACK")
        self.assertIsNone(self.journal.get("atomic-action"))

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

    def test_authorization_claim_is_exclusive_and_releasable(self):
        record = self.journal.reserve("approval-claim", "email.send", {"to": "a@example.com"})
        claimed = self.journal.claim_execution("approval-claim", record.request_digest)
        self.assertEqual(claimed.state, "authorizing")
        with self.assertRaises(ToolActionTransitionError):
            self.journal.claim_execution("approval-claim", record.request_digest)
        released = self.journal.release_authorization_claim("approval-claim", claimed.lease_owner)
        self.assertEqual(released.state, "prepared")
        self.assertIsNone(released.lease_owner)
        second = self.journal.claim_execution("approval-claim", record.request_digest)
        executing = self.journal.begin_claimed_execution("approval-claim", second.lease_owner)
        self.assertEqual(executing.state, "executing")
        with self.assertRaises(ToolActionTransitionError):
            self.journal.release_authorization_claim("approval-claim", second.lease_owner)

    def test_expired_authorization_claim_recovers_to_prepared_not_unknown(self):
        record = self.journal.reserve("approval-crash", "email.send", {"to": "a@example.com"})
        original_claim = self.journal.claim_execution("approval-crash", record.request_digest)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", "approval-crash"),
            )
            db.commit()
        recovered = self.journal.recover_expired_execution("approval-crash")
        self.assertEqual(recovered.state, "prepared")
        self.assertIsNone(recovered.lease_owner)

        # Expiry recovery revokes the old fencing token. A worker that resumes
        # after recovery cannot promote its stale authorization claim.
        with self.assertRaises(ToolActionTransitionError):
            self.journal.begin_claimed_execution(
                "approval-crash", original_claim.lease_owner,
            )

        # A new worker may acquire a fresh authorization token; the stale token
        # must not be interchangeable with the newly issued one.
        fresh_claim = self.journal.claim_execution("approval-crash", record.request_digest)
        self.assertEqual(fresh_claim.state, "authorizing")
        self.assertNotEqual(fresh_claim.lease_owner, original_claim.lease_owner)
        with self.assertRaises(ToolActionTransitionError):
            self.journal.begin_claimed_execution(
                "approval-crash", original_claim.lease_owner,
            )
        executing = self.journal.begin_claimed_execution(
            "approval-crash", fresh_claim.lease_owner,
        )
        self.assertEqual(executing.state, "executing")

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
        self.journal.mark_outcome_unknown(record.idempotency_key, self.journal.get(record.idempotency_key).lease_owner)
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
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key=?",
                ("2000-01-01T00:00:00+00:00", "lease-expired"),
            )
            db.commit()
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

    def test_bulk_recovery_counts_authorization_and_execution_claims(self):
        auth_record = self.journal.reserve("bulk-auth", "email.send", {"to": "a@example.com"})
        self.journal.claim_execution("bulk-auth", auth_record.request_digest)
        execution_record = self.journal.reserve("bulk-exec", "email.send", {"to": "b@example.com"})
        self.journal.begin_execution("bulk-exec", execution_record.request_digest)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE tool_actions SET lease_expires_at=? WHERE idempotency_key IN (?, ?)",
                ("2000-01-01T00:00:00+00:00", "bulk-auth", "bulk-exec"),
            )
            db.commit()
        self.assertEqual(self.journal.recover_expired_executions(), 2)
        self.assertEqual(self.journal.get("bulk-auth").state, "prepared")
        self.assertEqual(self.journal.get("bulk-exec").state, "outcome_unknown")

    def test_existing_journal_schema_is_upgraded_with_lease_columns(self):
        # setUp initializes the current schema; use a separate database to
        # accurately model a pre-lease database created by an older release.
        legacy_path = str(Path(self.temp_dir.name) / "legacy-actions.sqlite3")
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute(
                """CREATE TABLE tool_actions (
                    action_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL, request_digest TEXT NOT NULL,
                    state TEXT NOT NULL, result_json TEXT, error_code TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0
                )"""
            )
            db.execute(
                """INSERT INTO tool_actions
                   (action_id,idempotency_key,tool_name,request_digest,state,created_at,updated_at)
                   VALUES ('legacy-id','legacy-key','email.send',?,'prepared','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')""",
                ("a" * 64,),
            )
            db.commit()
        upgraded = ToolActionJournal(legacy_path).get("legacy-key")
        self.assertEqual(upgraded.state, "prepared")
        self.assertIsNone(upgraded.lease_owner)
        self.assertIsNone(upgraded.lease_expires_at)

    def test_legacy_state_check_constraint_is_rebuilt_without_losing_actions(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy-check-actions.sqlite3")
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute(
                """CREATE TABLE tool_actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('prepared','executing','succeeded','outcome_unknown','not_executed')),
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                )"""
            )
            db.execute(
                "CREATE INDEX idx_tool_actions_state_updated ON tool_actions(state, updated_at)"
            )
            db.executemany(
                """INSERT INTO tool_actions
                   (action_id,idempotency_key,tool_name,request_digest,state,created_at,
                    updated_at,version,lease_owner,lease_expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    ("legacy-prepared", "legacy-prepared", "email.send", "a" * 64,
                     "prepared", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                     0, None, None),
                    ("legacy-running", "legacy-running", "email.send", "b" * 64,
                     "executing", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                     3, "worker-lease", "2099-01-01T00:00:00+00:00"),
                ],
            )
            db.commit()

        upgraded = ToolActionJournal(legacy_path)
        prepared = upgraded.get("legacy-prepared")
        running = upgraded.get("legacy-running")
        self.assertEqual(prepared.state, "prepared")
        self.assertEqual(running.state, "executing")
        self.assertEqual(running.version, 3)
        self.assertEqual(running.lease_owner, "worker-lease")
        with closing(sqlite3.connect(legacy_path)) as db:
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_actions'"
            ).fetchone()[0]
            index = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_tool_actions_state_updated'"
            ).fetchone()
        self.assertIn("'authorizing'", schema)
        self.assertIsNotNone(index)

    def test_failed_legacy_migration_rolls_back_schema_and_index_changes(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy-migration-rollback.sqlite3")
        with closing(sqlite3.connect(legacy_path)) as db:
            db.execute(
                """CREATE TABLE tool_actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    tool_name TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                        ('prepared','executing','succeeded','outcome_unknown','not_executed')),
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                )"""
            )
            db.execute(
                "CREATE INDEX idx_tool_actions_state_updated ON tool_actions(state, updated_at)"
            )
            db.execute(
                """INSERT INTO tool_actions
                   (action_id,idempotency_key,tool_name,request_digest,state,created_at,updated_at)
                   VALUES ('rollback-action','rollback-key','email.send',?,'prepared',?,?)""",
                ("c" * 64, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            # Force the migration's table rename to fail after its index drop.
            db.execute("CREATE TABLE tool_actions_legacy_authorizing (sentinel TEXT)")
            db.commit()

        with self.assertRaises(sqlite3.OperationalError):
            ToolActionJournal(legacy_path)

        with closing(sqlite3.connect(legacy_path)) as db:
            schema = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_actions'"
            ).fetchone()[0]
            row = db.execute(
                "SELECT action_id,idempotency_key,state FROM tool_actions"
            ).fetchone()
            index = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_tool_actions_state_updated'"
            ).fetchone()
            blocker = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_actions_legacy_authorizing'"
            ).fetchone()
        self.assertNotIn("'authorizing'", schema)
        self.assertEqual(row, ("rollback-action", "rollback-key", "prepared"))
        self.assertIsNotNone(index)
        self.assertIsNotNone(blocker)

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
        record = self.journal.reserve("oversized-result", "email.send", {})
        started = self.journal.begin_execution("oversized-result", record.request_digest)
        with self.assertRaises(ValueError):
            self.journal.mark_succeeded("oversized-result", started.lease_owner, {"payload": "x" * 17000})


if __name__ == "__main__":
    unittest.main()
