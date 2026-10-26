import tempfile
import unittest
from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path

from main import build_agent, load_config
from core.permissions import PermissionGate


class CLICommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp_dir.name)
        self.config = load_config()
        self.config["workspace"] = str(self.workspace)
        self.agent = build_agent(self.config)
        self.agent.permission_gate = PermissionGate(lambda _: True, workspace=self.workspace)

    def tearDown(self):
        logger = logging.getLogger("yuki")
        for handler in list(logger.handlers):
            if isinstance(handler, RotatingFileHandler):
                logger.removeHandler(handler)
                handler.close()
        self.tmp_dir.cleanup()

    def test_registered_skills_execution(self):
        result = self.agent.run_skill("time", "")
        self.assertTrue(result.ok)

    def test_unknown_skill_execution_blocked(self):
        result = self.agent.run_skill("non_existent_skill", "")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "blocked")


if __name__ == "__main__":
    unittest.main()
