import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yuki_cli import run_cli


class CliMemoryTests(unittest.TestCase):
    def test_memory_commands_work_without_initializing_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"YUKI_MEMORY_PATH": str(Path(tmp) / "memory.json")}
            output = []
            with patch.dict(os.environ, env, clear=False):
                code = run_cli(["memory", "add", "Yuki project uses shared services", "--category", "lesson"],
                               provider_factory=lambda _: self.fail("provider must not initialize"),
                               output_fn=output.append)
                self.assertEqual(code, 0)
                saved = json.loads(output[-1])
                code = run_cli(["memory", "search", "shared services"],
                               provider_factory=lambda _: self.fail("provider must not initialize"),
                               output_fn=output.append)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output[-1])[0]["id"], saved["id"])
                code = run_cli(["memory", "delete", saved["id"]],
                               provider_factory=lambda _: self.fail("provider must not initialize"),
                               output_fn=output.append)
                self.assertEqual(code, 0)
                self.assertTrue(json.loads(output[-1])["deleted"])

    def test_memory_list_rejects_invalid_limit_safely(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"YUKI_MEMORY_PATH": str(Path(tmp) / "memory.json")}):
            output = []
            code = run_cli(["memory", "list", "--limit", "0"], output_fn=output.append)
            self.assertEqual(code, 4)
            self.assertNotIn(str(Path(tmp)), output[0])


if __name__ == "__main__":
    unittest.main()
