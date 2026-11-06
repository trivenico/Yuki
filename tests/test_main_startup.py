import os
import unittest
from unittest.mock import patch

import main


class MainStartupTests(unittest.TestCase):
    def test_local_api_can_be_disabled_for_terminal_only_mode(self):
        with patch.dict(os.environ, {"YUKI_START_LOCAL_API": "false"}):
            self.assertIsNone(main._start_local_api())

    def test_local_api_shutdown_accepts_missing_handle(self):
        self.assertIsNone(main._stop_local_api(None))


if __name__ == "__main__":
    unittest.main()
