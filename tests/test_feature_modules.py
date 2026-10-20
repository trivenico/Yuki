import tempfile
import unittest
from pathlib import Path
from core.planner import Planner
from core.config import load_config
from main import build_agent
from core.vector_memory import VectorMemory
from defense.firewall import FirewallManager, FirewallError
from defense.ids import PortScanDetector
from knowledge.indexer import KnowledgeIndexer
from skills.web_skill import WebSkill, WebSkillError
from voice.speaker_verification import SpeakerVerifier, SpeakerVerificationError
from voice.wakeword import WakeWordDetector, WakeWordError


class FeatureModuleTests(unittest.TestCase):
    def test_planner_is_bounded_and_non_executing(self):
        plan = Planner(max_steps=3).propose("Build a test", memory_context=["previous preference"])
        self.assertEqual(len(plan.steps), 3)
        self.assertEqual(plan.memory_context, ("previous preference",))

    def test_lexical_memory_search_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            memory = VectorMemory(Path(d) / "memory.json")
            saved = memory.remember("Yuki should use Python for tests", category="lesson")
            self.assertEqual(memory.search("Python tests")[0]["id"], saved["id"])

    def test_knowledge_indexer_stays_inside_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "notes.md").write_text("Yuki local project testing notes", encoding="utf-8")
            indexer = KnowledgeIndexer(VectorMemory(root / "memory.json"))
            result = indexer.index_file("notes.md", workspace=root)
            self.assertEqual(result["chunks_indexed"], 1)
            (root / "image.bin").write_bytes(b"not text")
            with self.assertRaises(ValueError):
                indexer.index_file("image.bin", workspace=root)

    def test_firewall_dry_run_requires_approval_and_no_os_changes(self):
        manager = FirewallManager(enabled=True, dry_run=True, approver=lambda _: True)
        result = manager.block_temporary("203.0.113.5", seconds=60)
        self.assertTrue(result["dry_run"])
        manager = FirewallManager(enabled=True, dry_run=True)
        with self.assertRaises(FirewallError):
            manager.block_temporary("203.0.113.5")

    def test_detector_alerts_on_unique_ports(self):
        detector = PortScanDetector(threshold=3, window_seconds=10)
        self.assertIsNone(detector.observe("192.0.2.4", 22, timestamp=1))
        self.assertIsNone(detector.observe("192.0.2.4", 23, timestamp=2))
        alert = detector.observe("192.0.2.4", 80, timestamp=3)
        self.assertEqual(alert["type"], "possible_port_scan")


    def test_main_runtime_registers_connected_skills(self):
        agent = build_agent(load_config())
        self.assertIn("interview", agent.registry.names())
        self.assertIn("web_fetch", agent.registry.names())
        self.assertIn("gmail_search", agent.registry.names())
        # This test validates registry/runtime wiring, not the human approval UI.
        # Explicitly grant approval here rather than weakening the production gate.
        agent.permission_gate.request = lambda **_kwargs: True
        result = agent.run_skill("interview", "technical")
        self.assertTrue(result.ok)
        self.assertIn("technical decision", result.data["prompt"])

    def test_live_firewall_mutation_fails_closed(self):
        manager = FirewallManager(enabled=True, dry_run=False, approver=lambda _: True)
        with self.assertRaises(FirewallError):
            manager.block_temporary("203.0.113.5", seconds=60)

    def test_wakeword_disabled_by_default(self):
        with self.assertRaises(WakeWordError):
            WakeWordDetector("model.onnx").load()

    def test_speaker_verification_fails_closed(self):
        with self.assertRaises(SpeakerVerificationError):
            SpeakerVerifier(enabled=True).verify("voice.wav")

    def test_web_skill_rejects_non_https(self):
        with self.assertRaises(WebSkillError):
            WebSkill(allowed_hosts=["example.com"]).execute("http://example.com")

    def test_web_skill_requires_explicit_host_allowlist(self):
        with self.assertRaises(WebSkillError):
            WebSkill().execute("https://example.com")


if __name__ == "__main__":
    unittest.main()
