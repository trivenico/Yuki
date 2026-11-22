import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_web


class WebRuntimeStartupTests(unittest.TestCase):
    def test_app_starts_without_constructing_configured_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "workspace": str(Path(directory) / "workspace"),
                "storage_backend": "sqlite",
                "provider": "openai",
            }
            with patch.object(run_web, "load_config", return_value=config), \
                 patch.object(run_web, "create_provider", side_effect=AssertionError(
                     "provider must not be constructed during API startup"
                 )):
                runtime = run_web.create_app()
            self.assertFalse(runtime.agent_loop.model.provider.initialized)
            runtime.close()
            self.assertTrue(runtime._closed)


if __name__ == "__main__":
    unittest.main()
