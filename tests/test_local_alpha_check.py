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
    def _run(self, *, config, environ=None, modules=None):
        with tempfile.TemporaryDirectory() as tmp:
            effective_config = {"provider": "openai", "workspace": tmp, **config}
            config_module = types.ModuleType("core.config")
            config_module.load_config = lambda: effective_config
            output = io.StringIO()
            env = dict(os.environ if environ is None else environ)
            with patch.dict(sys.modules, {"core.config": config_module}), \\
                 patch.object(
                     MODULE.importlib.util,
                     "find_spec",
                     side_effect=lambda name: (
                         None if modules is not None and name in modules and not modules[name]
                         else object()
                     ),
                 ), \\
                 patch.object(MODULE.os, "access", return_value=True), \\
                 patch.dict(MODULE.os.environ, env, clear=True), \\
                 contextlib.redirect_stdout(output):
                result = MODULE.main()
            return result, output.getvalue()

    def test_openai_local_alpha_prerequisites_pass_without_optional_requests(self):
        result, output = self._run(
            config={},
            environ={"OPENAI_API_KEY": "test-key"},
            modules={"yaml": True, "uvicorn": True, "requests": False},
        )
        self.assertEqual(result, 0)
        self.assertIn("READY FOR A LOCAL SMOKE TEST", output)
        self.assertNotIn("Dependency requests", output)

    def test_missing_cloud_key_fails_closed(self):
        result, output = self._run(config={}, environ={})
        self.assertEqual(result, 1)
        self.assertIn("OPENAI_API_KEY", output)

    def test_unsupported_provider_fails_closed(self):
        result, output = self._run(
            config={"provider": "unknown"},
            environ={"OPENAI_API_KEY": "test-key"},
        )
        self.assertEqual(result, 1)
        self.assertIn("unsupported provider: unknown", output)

    def test_missing_required_yaml_dependency_fails(self):
        result, output = self._run(
            config={},
            environ={"OPENAI_API_KEY": "test-key"},
            modules={"yaml": False, "uvicorn": True},
        )
        self.assertEqual(result, 1)
        self.assertIn("Dependency yaml", output)

    def test_hybrid_requires_local_ollama_model(self):
        result, output = self._run(
            config={"provider": "hybrid", "ollama_model": ""},
            environ={},
            modules={"yaml": True, "uvicorn": True},
        )
        self.assertEqual(result, 1)
        self.assertIn("Hybrid local model", output)
        self.assertIn("set ollama_model", output)

    def test_hybrid_local_only_mode_does_not_require_cloud_key(self):
        result, output = self._run(
            config={"provider": "hybrid", "ollama_model": "test-model"},
            environ={},
            modules={"yaml": True, "uvicorn": True},
        )
        self.assertEqual(result, 0)
        self.assertIn("local-only mode", output)


if __name__ == "__main__":
    unittest.main()
