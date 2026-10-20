import tempfile
import unittest
from pathlib import Path

from core.tiered_autonomy import TieredAutonomyPolicy
from skills.auto_personalizer import AutoPersonalizerSkill
from skills.chat_organizer import ChatOrganizerSkill
from skills.device_scanner import DeviceScannerSkill
from skills.financial_manager import FinancialManagerSkill
from skills.health_agent import HealthAgentSkill
from skills.home_security import HomeSecuritySkill
from skills.messaging_expanded import MessagingExpandedSkill
from skills.video_generator import VideoGeneratorSkill
from skills.canvas_workspace import CanvasWorkspaceSkill
from skills.smart_home_full import SmartHomeFullSkill
from skills.car_integration import CarIntegrationSkill
from skills.tiered_autonomy_skill import TieredAutonomySkill
from skills.registry import SkillRegistry


class PhaseCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_requested_skills_are_registered(self):
        registry = SkillRegistry.defaults(self.root)
        expected = {
            "video_generator",
            "auto_personalizer",
            "chat_organizer",
            "smart_home_full",
            "device_scanner",
            "car_integration",
            "canvas_workspace",
            "financial_manager",
            "health_agent",
            "home_security",
            "messaging_expanded",
            "tiered_autonomy",
        }
        self.assertTrue(expected.issubset(set(registry.names())))

    def test_video_job_is_not_misrepresented_as_rendered(self):
        result = VideoGeneratorSkill(self.root).execute('{"prompt":"test"}')
        self.assertTrue(result.ok)
        self.assertFalse(result.data["rendered"])
        self.assertEqual(result.data["backend"], "not_configured")

    def test_personalization_requires_opt_in(self):
        skill = AutoPersonalizerSkill(self.root)
        self.assertFalse(skill.execute('{"action":"record","signal":"prefers dark theme"}').ok)
        self.assertTrue(
            skill.execute('{"action":"record","signal":"prefers dark theme","opt_in":true}').ok
        )

    def test_chat_organizer_local_search(self):
        skill = ChatOrganizerSkill(self.root)
        self.assertTrue(skill.execute('{"title":"Build","text":"Yuki phase gaps"}').ok)
        self.assertEqual(len(skill.execute("search phase").data), 1)

    def test_device_scanner_never_claims_scan(self):
        result = DeviceScannerSkill().execute('{"device":"phone","updates_current":false}')
        self.assertTrue(result.ok)
        self.assertFalse(result.data["automated_vulnerability_scan"])

    def test_external_adapters_disabled_by_default(self):
        self.assertFalse(SmartHomeFullSkill().execute("status").ok)
        self.assertFalse(CarIntegrationSkill().execute("status").ok)

    def test_local_finance_health_security_and_messages(self):
        self.assertTrue(
            FinancialManagerSkill(self.root)
            .execute('{"description":"test","amount_minor":-50,"currency":"GBP"}')
            .ok
        )
        self.assertTrue(
            HealthAgentSkill(self.root).execute('{"note":"sleep 7h","date":"2026-10-17"}').ok
        )
        self.assertTrue(HomeSecuritySkill(self.root).execute('{"event":"door opened"}').ok)
        self.assertTrue(
            MessagingExpandedSkill(self.root).execute('{"recipient":"Alice","body":"draft"}').ok
        )

    def test_canvas_does_not_overwrite(self):
        skill = CanvasWorkspaceSkill(self.root)
        self.assertTrue(skill.execute("create Plan|First").ok)
        self.assertFalse(skill.execute("create Plan|Second").ok)

    def test_autonomy_never_bypasses_approval(self):
        policy = TieredAutonomyPolicy(2)
        self.assertFalse(policy.evaluate(changes_state=True, approved=False).allowed)
        self.assertTrue(policy.evaluate(changes_state=True, approved=True).allowed)
        self.assertFalse(policy.set_tier(2, approved=False).allowed)

    def test_tiered_autonomy_skill_requires_confirm_syntax(self):
        skill = TieredAutonomySkill(self.root)
        self.assertFalse(skill.execute("set 2").ok)
        self.assertTrue(skill.execute("confirm set 2").ok)


if __name__ == "__main__":
    unittest.main()
