import io
import unittest
from collections import defaultdict
from unittest.mock import patch

import main


class FakeReadinessReport:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return self.value


class ReadinessCommandTests(unittest.TestCase):
    def _run_command(self, report_side_effect=None, report_value=None):
        config = defaultdict(
            lambda: False,
            {"workspace": "/tmp/yuki-readiness-command-test", "max_agent_steps": 3},
        )
        output = io.StringIO()
        patches = [
            patch("builtins.input", side_effect=["/readiness", "/quit"]),
            patch("sys.stdout", new=output),
            patch.object(main, "load_config", return_value=config),
        ]
        for name in (
            "build_agent", "create_provider", "YukiAgent", "ApprovedTaskAgent",
            "MemoryStore", "VectorMemory", "KnowledgeIndexer", "Planner",
            "ReminderQueue", "DailyEvolutionCycle", "LocalTTS", "FasterWhisperSTT",
            "NmapRecon", "DockerSandbox", "FirewallManager", "PortScanDetector",
            "PacketSniffer", "ExploitRunner", "SpeakerVerifier",
        ):
            patches.append(patch.object(main, name))
        if report_side_effect is not None:
            readiness = patch("main.build_readiness_report", side_effect=report_side_effect)
        else:
            readiness = patch(
                "main.build_readiness_report",
                return_value=FakeReadinessReport(report_value),
            )
        patches.append(readiness)

        started = []
        try:
            for item in patches:
                started.append((item, item.start()))
            main.main()
            return output.getvalue()
        finally:
            for item, _mock in reversed(started):
                item.stop()

    def test_readiness_command_prints_platform_and_capabilities(self):
        fake = {
            "python": "3.11.9",
            "implementation": "CPython",
            "operating_system": "Linux",
            "architecture": "x86_64",
            "capabilities": {
                "ollama_server": {
                    "status": "verified",
                    "detail": "tags endpoint responded; inference not tested",
                }
            },
        }
        rendered = self._run_command(report_value=fake)
        self.assertIn("Yuki runtime: Python 3.11.9", rendered)
        self.assertIn("ollama_server: verified", rendered)
        self.assertIn("inference not tested", rendered)

    def test_readiness_command_handles_diagnostic_failure(self):
        rendered = self._run_command(report_side_effect=RuntimeError("diagnostic failure"))
        self.assertIn("Readiness diagnostics unavailable (RuntimeError)", rendered)


if __name__ == "__main__":
    unittest.main()
