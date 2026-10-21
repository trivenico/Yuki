import io
from collections import defaultdict
import unittest
from unittest.mock import patch

import main


class ReadinessCommandTests(unittest.TestCase):
    def test_readiness_command_prints_platform_and_capabilities(self):
        fake = {
            "python": "3.11.9",
            "implementation": "CPython",
            "operating_system": "Linux",
            "architecture": "x86_64",
            "capabilities": {
                "ollama_server": {"status": "verified", "detail": "tags endpoint responded; inference not tested"}
            },
        }
        with patch("builtins.input", side_effect=["/readiness", "/quit"]), \
             patch("sys.stdout", new_callable=io.StringIO) as output, \
             patch.object(main, "load_config", return_value=defaultdict(lambda: False, {"workspace": "/tmp/yuki-readiness-command-test", "max_agent_steps": 3})), \
             patch.object(main, "build_agent"), patch.object(main, "create_provider"), \
             patch.object(main, "YukiAgent"), patch.object(main, "ApprovedTaskAgent"), \
             patch.object(main, "MemoryStore"), patch.object(main, "VectorMemory"), \
             patch.object(main, "KnowledgeIndexer"), patch.object(main, "Planner"), \
             patch.object(main, "ReminderQueue"), patch.object(main, "DailyEvolutionCycle"), \
             patch.object(main, "LocalTTS"), patch.object(main, "FasterWhisperSTT"), \
             patch.object(main, "NmapRecon"), patch.object(main, "DockerSandbox"), \
             patch.object(main, "FirewallManager"), patch.object(main, "PortScanDetector"), \
             patch.object(main, "PacketSniffer"), patch.object(main, "ExploitRunner"), \
             patch.object(main, "SpeakerVerifier"), patch.object(main, "create_provider"), \
             patch.object(main, "build_readiness_report") as readiness:
            readiness.return_value.to_dict.return_value = fake
            main.main()
        rendered = output.getvalue()
        self.assertIn("Yuki runtime: Python 3.11.9", rendered)
        self.assertIn("ollama_server: verified", rendered)
        self.assertIn("inference not tested", rendered)

    def test_readiness_command_handles_diagnostic_failure(self):
        with patch("builtins.input", side_effect=["/readiness", "/quit"]), \
             patch("sys.stdout", new_callable=io.StringIO) as output, \
             patch.object(main, "load_config", return_value={"workspace": "/tmp/yuki-readiness-command-test"}), \
             patch.object(main, "build_agent"), patch.object(main, "create_provider"), \
             patch.object(main, "YukiAgent"), patch.object(main, "ApprovedTaskAgent"), \
             patch.object(main, "MemoryStore"), patch.object(main, "VectorMemory"), \
             patch.object(main, "KnowledgeIndexer"), patch.object(main, "Planner"), \
             patch.object(main, "ReminderQueue"), patch.object(main, "DailyEvolutionCycle"), \
             patch.object(main, "LocalTTS"), patch.object(main, "FasterWhisperSTT"), \
             patch.object(main, "NmapRecon"), patch.object(main, "DockerSandbox"), \
             patch.object(main, "FirewallManager"), patch.object(main, "PortScanDetector"), \
             patch.object(main, "PacketSniffer"), patch.object(main, "ExploitRunner"), \
             patch.object(main, "SpeakerVerifier"), \
             patch("main.build_readiness_report", side_effect=RuntimeError("diagnostic failure")):
            main.main()
        self.assertIn("Readiness diagnostics unavailable (RuntimeError)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
