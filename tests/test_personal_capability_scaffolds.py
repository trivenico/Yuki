import tempfile
import unittest
from pathlib import Path

from companion.identity_manager import IdentityManager
from companion.mood_engine import MoodEngine
from skills.content_marketer import ContentMarketerSkill
from skills.identity_skill import IdentitySkill
from skills.monetization_advisor import MonetizationAdvisorSkill
from skills.project_skill import ProjectSkill
from skills.registry import SkillRegistry
from skills.seo_optimizer import SEOOptimizerSkill
from skills.wiki_skill import WikiSkill


class PersonalCapabilityTests(unittest.TestCase):
    def test_identity_requires_consent_and_never_stores_biometrics(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = IdentityManager(Path(directory) / "identities.json")
            with self.assertRaises(PermissionError):
                manager.add("Friend")
            saved = manager.add("Friend", consent=True)
            self.assertTrue(saved["consent"])
            self.assertFalse(saved["biometric_data_stored"])
            self.assertTrue(manager.delete(saved["id"]))
            self.assertFalse(manager.delete(saved["id"]))

    def test_mood_engine_only_uses_explicit_self_report(self):
        engine = MoodEngine()
        self.assertIsNone(engine.current())
        mood = engine.set_mood("sad")
        self.assertEqual(mood.source, "user_report")
        self.assertEqual(engine.adapt("hello")["style"], "gentle and supportive")
        engine.clear()
        self.assertIsNone(engine.current())
        with self.assertRaises(ValueError):
            engine.set_mood("diagnose")

    def test_project_and_marketing_skills_are_plan_only(self):
        project = ProjectSkill().run("Build a small personal website")
        self.assertTrue(project.ok)
        self.assertIn("nothing was pushed", project.message)
        content = ContentMarketerSkill().run("Helpful guide for beginner gardeners")
        self.assertTrue(content.ok)
        self.assertIn("no web research", content.message)
        monetization = MonetizationAdvisorSkill().run("Compare two service ideas")
        self.assertTrue(monetization.ok)
        self.assertIn("not guaranteed", monetization.message)

    def test_seo_audit_is_heuristic_and_requires_text(self):
        skill = SEOOptimizerSkill()
        self.assertFalse(skill.run("short").ok)
        result = skill.run("# Useful guide\n\n" + "Helpful original information. " * 10)
        self.assertTrue(result.ok)
        self.assertIn("word_count", result.data)

    def test_identity_skill_does_not_enroll_people_without_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = IdentitySkill(IdentityManager(Path(directory) / "identities.json"))
            result = skill.run("add Friend")
            self.assertFalse(result.ok)
            self.assertEqual(skill.manager.list(), [])

    def test_registry_includes_new_personal_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            names = SkillRegistry.defaults(Path(directory)).names()
            for name in ("wiki", "identity", "project", "content_marketer", "seo_audit", "monetization", "course_coach"):
                self.assertIn(name, names)

    def test_wiki_skill_fails_closed_when_not_allowlisted(self):
        result = WikiSkill(allowed_hosts=()).run("Artificial_intelligence")
        self.assertFalse(result.ok)
        self.assertIn("not enabled", result.message)


if __name__ == "__main__":
    unittest.main()
