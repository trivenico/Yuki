import os
import tempfile
import unittest
from pathlib import Path

from core import __version__
from core.config import load_dotenv


class PackagingTests(unittest.TestCase):
    def test_version_string(self):
        self.assertEqual(__version__, "0.2.0")

    def test_load_dotenv_parses_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("TEST_DOTENV_KEY=custom_val_123\n# comment\n", encoding="utf-8")
            load_dotenv(env_file)
            self.assertEqual(os.environ.get("TEST_DOTENV_KEY"), "custom_val_123")


if __name__ == "__main__":
    unittest.main()
