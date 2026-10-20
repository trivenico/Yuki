"""Phase 43 regression tests for production sandbox enforcement."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest
from core.sqlite_action_approvals import SQLiteActionApprovalStore
from core.subprocess_executor import SubprocessExecutor


def manifest() -> SkillManifest:
    return SkillManifest.from_dict({
        "skill_id": "test.echo",
        "version": "1.0.0",
        "entrypoint": "test_skill.main",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 128}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 128}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "capabilities": [],
        "risk": "low",
        "timeout_seconds": 2,
        "max_output_bytes": 1024,
    })


class DeclaredSandboxExecutor(SubprocessExecutor):
    """Test double for a backend that explicitly declares enforced OS isolation."""
    sandbox_enforced = True


class Phase43ProductionSandboxGateTests(unittest.TestCase):
    def test_production_rejects_plain_subprocess_executor(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteActionApprovalStore(str(Path(directory) / "approvals.sqlite3"))
            with patch.dict(os.environ, {"YUKI_ENV": "production"}):
                dispatcher = SkillDispatcher(approval_store=store)
                with self.assertRaisesRegex(RuntimeError, "OS-sandboxed executor"):
                    dispatcher.register_subprocess(
                        manifest(), ["python", "-m", "test_skill"],
                        executor=SubprocessExecutor(),
                    )

    def test_production_accepts_executor_declaring_enforced_sandbox(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteActionApprovalStore(str(Path(directory) / "approvals.sqlite3"))
            with patch.dict(os.environ, {"YUKI_ENV": "production"}):
                dispatcher = SkillDispatcher(approval_store=store)
                dispatcher.register_subprocess(
                    manifest(), ["python", "-m", "test_skill"],
                    executor=DeclaredSandboxExecutor(),
                )
                self.assertIsNotNone(dispatcher.describe("test.echo"))

    def test_development_remains_compatible_with_trusted_subprocesses(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            dispatcher = SkillDispatcher()
            dispatcher.register_subprocess(
                manifest(), ["python", "-m", "test_skill"],
                executor=SubprocessExecutor(),
            )
            self.assertIsNotNone(dispatcher.describe("test.echo"))


if __name__ == "__main__":
    unittest.main()
