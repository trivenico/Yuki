import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config
from core.permissions import PermissionGate
from core.agent import YukiCoreAgent
from skills import TimeSkill


class RuntimeIntegrationTests(unittest.TestCase):
    def test_config_loads_safe_defaults(self):
        config = load_config()
        self.assertEqual(config["name"], "Yuki")
        self.assertFalse(config["voice_enabled"])
        self.assertGreaterEqual(config["max_agent_steps"], 1)
        self.assertLessEqual(config["max_agent_steps"], 5)

    def test_workspace_environment_override(self):
        with patch.dict(os.environ, {"YUKI_WORKSPACE": "./test-workspace-override"}):
            config = load_config()
        self.assertTrue(config["workspace"].endswith("test-workspace-override"))

    def test_yaml_invalid_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("max_agent_steps: not-an-integer\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_config(path)

    def test_skill_requires_explicit_approval(self):
        agent = YukiCoreAgent([TimeSkill()], PermissionGate())
        result = agent.run_skill("time")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "permission_denied")

    def test_unknown_skill_is_not_executed(self):
        agent = YukiCoreAgent([TimeSkill()], PermissionGate(lambda _: True))
        result = agent.run_skill("shell", "whoami")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "blocked")


if __name__ == "__main__":
    unittest.main()
