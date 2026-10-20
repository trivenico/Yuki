"""P0 regressions for issue #9: imports, registry construction, and provider validation."""
from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers.factory import create_provider
from skills.registry import SkillRegistry


class P0Issue9RegressionTests(unittest.TestCase):
    def test_approval_flow_dependency_modules_import_together(self):
        modules = (
            "core.action_approvals",
            "core.approval_flow",
            "core.approval_audit",
            "core.pending_approvals",
            "core.manifest_task_agent",
            "core.skill_dispatch",
            "core.skill_manifest",
            "core.subprocess_executor",
        )
        for module in modules:
            with self.subTest(module=module):
                self.assertIsNotNone(importlib.import_module(module))

    def test_default_registry_constructs_device_scanner_without_workspace_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = SkillRegistry.defaults(Path(directory))
        self.assertIsNotNone(registry.get("device_scanner"))

    def test_unknown_provider_is_rejected_before_ollama_construction(self):
        with patch("providers.factory.OllamaProvider") as ollama:
            with self.assertRaisesRegex(ValueError, "provider must be one of"):
                create_provider({"provider": "not-a-provider", "ollama_model": ""}, environ={})
            ollama.assert_not_called()


if __name__ == "__main__":
    unittest.main()
