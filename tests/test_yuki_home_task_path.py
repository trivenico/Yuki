import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki_tasks


class YukiHomeTaskPathTests(unittest.TestCase):
    def test_task_file_uses_yuki_home_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"YUKI_HOME": tmp}):
                expected = Path(tmp).resolve() / "yuki_tasks.json"
                self.assertEqual(yuki_tasks.data_root(), Path(tmp).resolve())
                self.assertEqual(yuki_tasks.TASKS_FILE, expected)

    def test_default_task_path_remains_repository_root_compatible(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                yuki_tasks.TASKS_FILE,
                Path(yuki_tasks.__file__).resolve().parent / "yuki_tasks.json",
            )


if __name__ == "__main__":
    unittest.main()
