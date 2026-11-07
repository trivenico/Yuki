"""Tests for the isolated local memory persistence acceptance command."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "memory_acceptance.py"
SPEC = importlib.util.spec_from_file_location("yuki_memory_acceptance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MemoryAcceptanceTests(unittest.TestCase):
    def test_sqlite_memory_survives_store_reopen_and_can_be_deleted(self):
        result = MODULE.run_acceptance()
        self.assertEqual(result["result"], "passed")
        self.assertIn("persistence_after_separate_process_restart", result["checks"])
        self.assertIn("persistence_after_store_reopen", result["checks"])
        self.assertIn("actor_scope_isolation", result["checks"])
        self.assertIn("authorized_memory_deletion", result["checks"])
        self.assertFalse(result["database_or_memory_content_logged"])

    def test_memory_content_is_not_in_acceptance_result(self):
        result = MODULE.run_acceptance()
        self.assertNotIn("content", result)
        self.assertNotIn("memory_id", result)


if __name__ == "__main__":
    unittest.main()
