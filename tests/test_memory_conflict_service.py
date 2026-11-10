import tempfile
import unittest
from pathlib import Path

from core.conflict_ledger import ConflictLedger
from core.memory_conflicts import MemoryVersion, reconcile_memory_versions
from core.memory_conflict_service import persist_reconciliation


class MemoryConflictServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = ConflictLedger(Path(self.tempdir.name) / "conflicts.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def versions(self):
        local = MemoryVersion("mem-1", "v-local", "device-a", 1, "Local edit", parent_version_id="v-root")
        incoming = MemoryVersion("mem-1", "v-remote", "device-b", 1, "Remote edit", parent_version_id="v-root")
        return local, incoming

    def test_concurrent_versions_are_persisted_and_idempotent(self):
        local, incoming = self.versions()
        reconciliation = reconcile_memory_versions(local, incoming)
        first = persist_reconciliation(self.ledger, reconciliation, created_at="2026-01-01T00:00:00Z")
        second = persist_reconciliation(self.ledger, reconciliation, created_at="2026-01-01T00:00:00Z")
        self.assertEqual(first.conflict_id, second.conflict_id)
        self.assertEqual(first.status, "open")
        self.assertEqual({v["version_id"] for v in first.versions}, {"v-local", "v-remote"})

    def test_identical_and_sequential_versions_are_not_recorded(self):
        local, incoming = self.versions()
        self.assertIsNone(persist_reconciliation(self.ledger, reconcile_memory_versions(local, local)))
        child = MemoryVersion("mem-1", "v-child", "device-a", 2, "Follow-up", parent_version_id="v-local")
        self.assertIsNone(persist_reconciliation(self.ledger, reconcile_memory_versions(local, child)))

    def test_malformed_reconciliation_is_rejected(self):
        with self.assertRaises(TypeError):
            persist_reconciliation(self.ledger, {"status": "conflict"})

if __name__ == "__main__":
    unittest.main()
