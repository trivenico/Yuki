"""Regression tests for the local alpha readiness command."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "local_alpha_check.py"
SPEC = importlib.util.spec_from_file_location("yuki_local_alpha_check", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LocalAlphaCheckTests(unittest.TestCase):
    def _run(self, *, api_key):
        with tempfile.TemporaryDirectory() as tmp:
            config_module = types.ModuleType("core.config")
            config_module.load_config = lambda: {
                "provider": "openai", "workspace": tmp,
            }
            output = io.StringIO()
            env = dict(os.environ)
            if api_key:
                env["OPENAI_API_KEY"] = "test-key"
            else:
                env.pop("OPENAI_API_KEY", None)
            with patch.dict(sys.modules, {"core.config": config_module}), \
                 patch.object(MODULE.importlib.util, "find_spec", return_value=object()), \
                 patch.object(MODULE.os, "access", return_value=True), \
                 patch.dict(MODULE.os.environ, env, clear=True), \
                 contextlib.redirect_stdout(output):
                result = MODULE.main()
            return result, output.getvalue()

    def test_openai_local_alpha_prerequisites_pass(self):
        result, output = self._run(api_key=True)
        self.assertEqual(result, 0)
        self.assertIn("READY FOR A LOCAL SMOKE TEST", output)

    def test_missing_cloud_key_fails_closed(self):
        result, output = self._run(api_key=False)
        self.assertEqual(result, 1)
        self.assertIn("OPENAI_API_KEY", output)


if __name__ == "__main__":
    unittest.main()
