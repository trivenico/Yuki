import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.portable_paths import config_path


class UserConfigPrecedenceTests(unittest.TestCase):
    def test_user_config_created_by_setup_takes_precedence_over_checkout_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            user_config = Path(directory) / "config.yaml"
            user_config.write_text("provider: ollama\n", encoding="utf-8")
            with patch("core.portable_paths._user_config_root", return_value=Path(directory)):
                with patch.dict(os.environ, {}, clear=True):
                    selected = config_path()
            self.assertEqual(selected, user_config.resolve())


if __name__ == "__main__":
    unittest.main()
