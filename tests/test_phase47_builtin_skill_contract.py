"""Phase 47 contract and real subprocess integration tests."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from core.action_approvals import ActionApprovalStore
from core.builtin_skill_adapters import time_skill_command, time_skill_manifest
from core.skill_dispatch import SkillDispatcher
from core.subprocess_executor import SubprocessExecutor
from core.skill_manifest import SkillManifest


class Phase47BuiltinSkillContractTests(unittest.TestCase):
    def test_manifest_is_versioned_and_strict(self):
        manifest = time_skill_manifest()
        self.assertEqual(manifest.skill_id, "system.time.read")
        self.assertEqual(manifest.version, "1.0.0")
        self.assertEqual(manifest.capabilities, ("system.clock.read",))
        self.assertFalse(SkillManifest.validate_object(manifest.input_schema, {"extra": 1}))
        self.assertTrue(SkillManifest.validate_object(manifest.input_schema, {}))

    def test_real_subprocess_invocation_returns_contract_valid_output(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            dispatcher = SkillDispatcher(
                capability_grants=("system.clock.read",),
                approval_store=ActionApprovalStore(),
            )
            manifest = time_skill_manifest()
            dispatcher.register_subprocess(
                manifest, time_skill_command(),
                executor=SubprocessExecutor(timeout_seconds=5, max_output_bytes=8192),
            )
            approval = dispatcher.request_approval("system.time.read", {})
            consumed = dispatcher.execute(
                "system.time.read", {}, approval_id=approval["approval_id"],
            )
        self.assertTrue(consumed["ok"])
        self.assertEqual(consumed["skill_id"], "system.time.read")
        self.assertEqual(set(consumed["output"]), {"local_time"})
        self.assertIsInstance(consumed["output"]["local_time"], str)
        self.assertGreater(len(consumed["output"]["local_time"]), 0)

    def test_missing_capability_prevents_approval_creation(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            dispatcher = SkillDispatcher(approval_store=ActionApprovalStore())
            dispatcher.register_subprocess(
                time_skill_manifest(), time_skill_command(), executor=SubprocessExecutor(),
            )
            with self.assertRaisesRegex(PermissionError, "capability"):
                dispatcher.request_approval("system.time.read", {})

    def test_nonempty_arguments_are_rejected(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            dispatcher = SkillDispatcher(
                capability_grants=("system.clock.read",),
                approval_store=ActionApprovalStore(),
            )
            dispatcher.register_subprocess(
                time_skill_manifest(), time_skill_command(), executor=SubprocessExecutor(),
            )
            with self.assertRaisesRegex(ValueError, "schema"):
                dispatcher.request_approval("system.time.read", {"module": "os"})

    def test_production_still_refuses_registration(self):
        with patch.dict(os.environ, {"YUKI_ENV": "production"}):
            dispatcher = SkillDispatcher(
                capability_grants=("system.clock.read",),
                approval_store=ActionApprovalStore(),
            )
            with self.assertRaisesRegex(RuntimeError, "verified OS sandbox"):
                dispatcher.register_subprocess(
                    time_skill_manifest(), time_skill_command(), executor=SubprocessExecutor(),
                )


if __name__ == "__main__":
    unittest.main()
