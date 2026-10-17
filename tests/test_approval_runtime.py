import os
import tempfile
import unittest
from pathlib import Path

from core.approval_runtime import build_local_approval_runtime


class LocalApprovalRuntimeTests(unittest.TestCase):
    def test_builds_persistent_stores_in_private_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = build_local_approval_runtime(tmp)
            state_dir = Path(tmp) / ".yuki" / "state"
            self.assertTrue((state_dir / "pending-approvals.sqlite3").exists())
            self.assertTrue((state_dir / "approval-audit.sqlite3").exists())
            self.assertTrue(runtime.actor)
            self.assertTrue(runtime.session_id)
            self.assertEqual(runtime.coordinator.pending_store.database_path,
                             str(state_dir / "pending-approvals.sqlite3"))
            self.assertEqual(runtime.coordinator.audit_log.database_path,
                             str(state_dir / "approval-audit.sqlite3"))
            if os.name == "posix":
                self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
                self.assertEqual(Path(runtime.coordinator.pending_store.database_path).stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(runtime.coordinator.audit_log.database_path).stat().st_mode & 0o777, 0o600)

    def test_each_process_runtime_gets_distinct_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = build_local_approval_runtime(tmp)
            second = build_local_approval_runtime(tmp)
            self.assertEqual(first.actor, second.actor)
            self.assertNotEqual(first.session_id, second.session_id)

    def test_invalid_store_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                build_local_approval_runtime(tmp, ttl_seconds=0)


if __name__ == "__main__":
    unittest.main()
