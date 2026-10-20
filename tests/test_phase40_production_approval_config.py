"""Production composition safety checks for the skill dispatcher."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.skill_dispatch import SkillDispatcher
from core.sqlite_action_approvals import SQLiteActionApprovalStore


class ProductionApprovalConfigTests(unittest.TestCase):
    def test_production_refuses_implicit_in_memory_approvals(self):
        with patch.dict(os.environ, {"YUKI_ENV": "production"}):
            with self.assertRaisesRegex(RuntimeError, "durable approval store"):
                SkillDispatcher()

    def test_production_accepts_explicit_sqlite_store(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteActionApprovalStore(str(Path(directory) / "approvals.sqlite3"))
            with patch.dict(os.environ, {"YUKI_ENV": "production"}):
                dispatcher = SkillDispatcher(approval_store=store)
            self.assertIs(dispatcher.approval_store, store)

    def test_development_retains_compatible_memory_default(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            dispatcher = SkillDispatcher()
        self.assertEqual(type(dispatcher.approval_store).__name__, "ActionApprovalStore")

    def test_invalid_store_interface_is_rejected(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            with self.assertRaisesRegex(TypeError, "create"):
                SkillDispatcher(approval_store=object())


if __name__ == "__main__":
    unittest.main()
