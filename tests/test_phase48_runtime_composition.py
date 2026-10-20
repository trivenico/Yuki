"""Phase 48 runtime composition and durable approval-store tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.runtime_composition import build_skill_dispatcher
from core.sqlite_action_approvals import SQLiteActionApprovalStore


class Phase48RuntimeCompositionTests(unittest.TestCase):
    def test_development_composition_registers_only_the_clock_contract(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_ENV": "development"}):
            dispatcher = build_skill_dispatcher(directory, capability_grants=("system.clock.read",))
            self.assertEqual(dispatcher.describe("system.time.read")["version"], "1.0.0")
            self.assertIsInstance(dispatcher.approval_store, SQLiteActionApprovalStore)

    def test_production_composition_uses_durable_store_but_registers_no_executor(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_ENV": "production"}):
            dispatcher = build_skill_dispatcher(directory, capability_grants=("system.clock.read",))
            self.assertIsInstance(dispatcher.approval_store, SQLiteActionApprovalStore)
            self.assertIsNone(dispatcher.describe("system.time.read"))
            self.assertTrue(Path(directory, ".yuki-security", "action_approvals.sqlite3").is_file())

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits only")
    def test_approval_storage_permissions_are_restrictive(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_ENV": "production"}):
            build_skill_dispatcher(directory)
            security_dir = Path(directory, ".yuki-security")
            database = security_dir / "action_approvals.sqlite3"
            self.assertEqual(security_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_approval_store_rejects_database_symlink_before_open(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory, "target.sqlite3")
            target.write_bytes(b"not a database")
            link = Path(directory, "approvals.sqlite3")
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                SQLiteActionApprovalStore(str(link))
            self.assertEqual(target.read_bytes(), b"not a database")


if __name__ == "__main__":
    unittest.main()
