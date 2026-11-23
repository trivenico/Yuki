import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore


class MemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / "memory.json")
        self.entry = self.store.add("A reviewed operational lesson.", category="lesson")

    def tearDown(self):
        self.temp.cleanup()

    def test_update_is_versioned_and_audited(self):
        changed = self.store.update_lifecycle(
            self.entry["id"], expected_version=1, confidence=0.9,
            status="archived", reason="Superseded by a reviewed procedure.",
        )
        self.assertEqual(changed["version"], 2)
        self.assertEqual(changed["confidence"], 0.9)
        self.assertEqual(changed["status"], "archived")
        self.assertEqual(len(changed["lifecycle_history"]), 1)
        self.assertEqual(changed["text"], self.entry["text"])

    def test_stale_version_cannot_overwrite_newer_state(self):
        self.store.update_lifecycle(
            self.entry["id"], expected_version=1, status="archived",
            reason="Review archive.",
        )
        with self.assertRaisesRegex(RuntimeError, "version conflict"):
            self.store.update_lifecycle(
                self.entry["id"], expected_version=1, status="active",
                reason="Stale update.",
            )

    def test_rollback_restores_state_without_erasing_audit(self):
        self.store.update_lifecycle(
            self.entry["id"], expected_version=1, confidence=0.9,
            status="archived", reason="Temporary archive.",
        )
        restored = self.store.rollback_lifecycle(
            self.entry["id"], expected_version=2, reason="Review reversed the archive.",
        )
        self.assertEqual(restored["version"], 3)
        self.assertEqual(restored["confidence"], 0.5)
        self.assertEqual(restored["status"], "active")
        self.assertEqual([event["action"] for event in restored["lifecycle_history"]], ["update", "rollback"])

    def test_rejects_invalid_confidence_and_status(self):
        with self.assertRaises(ValueError):
            self.store.update_lifecycle(
                self.entry["id"], expected_version=1, confidence=float("nan"),
                reason="Invalid confidence.",
            )
        with self.assertRaises(ValueError):
            self.store.update_lifecycle(
                self.entry["id"], expected_version=1, status="deleted",
                reason="Invalid state.",
            )


if __name__ == "__main__":
    unittest.main()
