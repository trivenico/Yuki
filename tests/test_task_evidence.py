import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.task_evidence import EvidenceAttestation, TaskEvidenceStore


class TaskEvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.accepted = True
        self.seen = []
        self.store = TaskEvidenceStore(
            str(Path(self.tmp.name) / "evidence.sqlite3"),
            attestation_verifier=lambda item: self.seen.append(item) or self.accepted,
        )
        self.attestation = EvidenceAttestation(
            owner_id="alice", task_id="task-1", task_version=3, kind="ci_workflow",
            reference="ci:workflow/123", digest_sha256="a" * 64,
            verifier_id="github-actions-prod", verified_at="2026-11-23T10:00:00+04:00",
        )

    def test_requires_trusted_verifier_acceptance(self):
        self.accepted = False
        with self.assertRaises(PermissionError):
            self.store.record_verified(self.attestation)
        self.assertEqual(self.store.list_verified(owner_id="alice", task_id="task-1"), ())

    def test_verifier_receives_normalized_attestation_and_record_is_durable(self):
        record = self.store.record_verified(self.attestation)
        self.assertEqual(record.owner_id, "alice")
        self.assertEqual(record.task_version, 3)
        self.assertEqual(record.verified_at, "2026-11-23T06:00:00+00:00")
        self.assertEqual(self.seen[0].digest_sha256, "a" * 64)
        reopened = TaskEvidenceStore(
            str(Path(self.tmp.name) / "evidence.sqlite3"),
            attestation_verifier=lambda _: True,
        )
        self.assertEqual(reopened.list_verified(owner_id="alice", task_id="task-1"), (record,))
        self.assertEqual(reopened.list_verified(owner_id="alice", task_id="task-1", task_version=2), ())
        self.assertEqual(reopened.list_verified(owner_id="alice", task_id="task-1", task_version=3), (record,))

    def test_duplicate_attestation_is_idempotent(self):
        first = self.store.record_verified(self.attestation)
        second = self.store.record_verified(self.attestation)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.list_verified(owner_id="alice", task_id="task-1")), 1)

    def test_owner_and_task_scoping(self):
        self.store.record_verified(self.attestation)
        self.assertEqual(self.store.list_verified(owner_id="bob", task_id="task-1"), ())
        self.assertEqual(self.store.list_verified(owner_id="alice", task_id="task-2"), ())

    def test_rejects_unsupported_kind_bad_digest_and_naive_timestamp(self):
        for changes in (
            {"kind": "agent_claim"},
            {"task_version": 0},
            {"digest_sha256": "not-a-digest"},
            {"verified_at": "2026-11-23T10:00:00"},
        ):
            values = {field: getattr(self.attestation, field) for field in self.attestation.__dataclass_fields__}
            values.update(changes)
            with self.assertRaises(ValueError):
                self.store.record_verified(EvidenceAttestation(**values))

    def test_evidence_rows_are_append_only(self):
        self.store.record_verified(self.attestation)
        with sqlite3.connect(self.store.database_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM task_evidence")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE task_evidence SET reference='forged'")


    def test_migrates_earlier_ledger_rows_to_version_one(self):
        legacy_path = Path(self.tmp.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as db:
            db.execute("""
                CREATE TABLE task_evidence (
                    evidence_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    reference TEXT NOT NULL, digest_sha256 TEXT NOT NULL,
                    verifier_id TEXT NOT NULL, verified_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(owner_id, task_id, digest_sha256, verifier_id)
                )
            """)
            db.execute(
                "INSERT INTO task_evidence VALUES(?,?,?,?,?,?,?,?,?)",
                ("evd_legacy", "alice", "task-legacy", "ci_workflow",
                 "ci:legacy", "d" * 64, "trusted-ci",
                 "2026-11-22T00:00:00+00:00", "2026-11-22T00:01:00+00:00"),
            )
        migrated = TaskEvidenceStore(str(legacy_path), attestation_verifier=lambda _: True)
        records = migrated.list_verified(owner_id="alice", task_id="task-legacy", task_version=1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].evidence_id, "evd_legacy")
        self.assertEqual(records[0].task_version, 1)

    def test_verifier_exception_fails_closed(self):
        store = TaskEvidenceStore(
            str(Path(self.tmp.name) / "failing.sqlite3"),
            attestation_verifier=lambda _: (_ for _ in ()).throw(RuntimeError("private")),
        )
        with self.assertRaises(PermissionError):
            store.record_verified(self.attestation)
        self.assertEqual(store.list_verified(owner_id="alice", task_id="task-1"), ())


if __name__ == "__main__":
    unittest.main()
