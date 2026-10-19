import tempfile
import unittest
from pathlib import Path

from companion.heartbeat import HeartbeatRunner
from providers.mock import MockProvider
from yuki_agent import YukiAgent


class HeartbeatTests(unittest.TestCase):
    def test_heartbeat_runner_parses_heartbeat_md_and_saves_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            hb_file = workspace / "HEARTBEAT.md"
            hb_file.write_text("- How is system memory status?\n- Check workspace notes\n", encoding="utf-8")

            ai = YukiAgent(MockProvider())
            runner = HeartbeatRunner(workspace, ai=ai, interval_seconds=900, enabled=True)
            res = runner.run_check_once()

            self.assertEqual(res["checks"], 2)
            self.assertIsNotNone(runner.store.get("last_run"))

            logs_dir = workspace / "logs"
            self.assertTrue(logs_dir.exists())
            log_files = list(logs_dir.glob("heartbeat-*.md"))
            self.assertEqual(len(log_files), 1)
            content = log_files[0].read_text(encoding="utf-8")
            self.assertIn("How is system memory status?", content)


if __name__ == "__main__":
    unittest.main()
