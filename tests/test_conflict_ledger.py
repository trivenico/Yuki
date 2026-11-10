import tempfile
import unittest
from pathlib import Path

from core.conflict_ledger import ConflictLedger, ConflictLedgerError, InvalidConflict


class ConflictLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = ConflictLedger(Path(self.temp.name) / "conflicts.sqlite3")
        self.versions = [
            {"version_id": "v2-local", "origin_id": "laptop", "sequence": 2, "text": "local edit"},
            {"version_id": "v2-cloud", "origin_id": "cloud", "sequence": 2, "text": "cloud edit"},
        ]
        self.conflict = {
            "conflict_id": "conflict-1", "memory_id": "note-1",
            "versions": self.versions, "created_at": "2026-10-23T12:00:00+00:00",
        }

    def test_record_is_durable_idempotent_and_preserves_both_versions(self):
        first = self.ledger.record_conflict(**self.conflict)
        second = self.ledger.record_conflict(**self.conflict)
        self.assertEqual(first, second)
        self.assertEqual(first.status, "open")
        self.assertEqual(first.versions, tuple(self.versions))
        reopened = ConflictLedger(self.ledger.database_path).get("conflict-1")
        self.assertEqual(reopened.versions, tuple(self.versions))
        self.assertEqual(len(self.ledger.audit_events("conflict-1")), 1)

    def test_same_conflict_id_with_changed_content_is_rejected(self):
        self.ledger.record_conflict(**self.conflict)
        changed = dict(self.conflict, versions=[self.versions[0], dict(self.versions[1], text="tampered")])
        with self.assertRaises(ConflictLedgerError):
            self.ledger.record_conflict(**changed)

    def test_resolution_requires_a_member_version_and_actor(self):
        self.ledger.record_conflict(**self.conflict)
        with self.assertRaises(InvalidConflict):
            self.ledger.resolve("conflict-1", chosen_version_id="unknown", actor="owner",
                                resolved_at="2026-10-23T12:01:00+00:00")
        with self.assertRaises(InvalidConflict):
            self.ledger.resolve("conflict-1", chosen_version_id="v2-local", actor="",
                                resolved_at="2026-10-23T12:01:00+00:00")

    def test_resolution_is_audited_and_cannot_be_replaced(self):
        self.ledger.record_conflict(**self.conflict)
        resolved = self.ledger.resolve(
            "conflict-1", chosen_version_id="v2-local", actor="owner",
            resolved_at="2026-10-23T12:01:00+00:00", note="Reviewed both edits",
        )
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.resolved_version_id, "v2-local")
        self.assertEqual(self.ledger.list_open(), [])
        self.assertEqual([e["action"] for e in self.ledger.audit_events("conflict-1")],
                         ["conflict_recorded", "conflict_resolved"])
        with self.assertRaises(ConflictLedgerError):
            self.ledger.resolve(
                "conflict-1", chosen_version_id="v2-cloud", actor="owner",
                resolved_at="2026-10-23T12:02:00+00:00",
            )

    def test_exact_resolution_retry_is_idempotent(self):
        self.ledger.record_conflict(**self.conflict)
        kwargs = dict(chosen_version_id="v2-local", actor="owner",
                      resolved_at="2026-10-23T12:01:00+00:00", note="Reviewed")
        first = self.ledger.resolve("conflict-1", **kwargs)
        second = self.ledger.resolve("conflict-1", **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(len(self.ledger.audit_events("conflict-1")), 2)

    def test_invalid_versions_and_limits_fail_closed(self):
        for versions in ([], [{"version_id": "v1"}], [{"version_id": "x"}, {"version_id": "x"}]):
            with self.subTest(versions=versions):
                with self.assertRaises(InvalidConflict):
                    self.ledger.record_conflict(
                        conflict_id="conflict-bad", memory_id="note-1",
                        versions=versions, created_at="now",
                    )
        with self.assertRaises(InvalidConflict):
            self.ledger.record_conflict(
                conflict_id="conflict-big", memory_id="note-1",
                versions=[{"version_id":"v1","text":"x" * 20000},{"version_id":"v2"}],
                created_at="now",
            )


if __name__ == "__main__":
    unittest.main()
