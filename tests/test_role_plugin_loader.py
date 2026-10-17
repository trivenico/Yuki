import unittest
from core.plugin_loader import PluginLoader
from core.role_engine import RoleEngine
from skills.role_skill import RoleSkill
from skills.registry import SkillRegistry
import tempfile
from pathlib import Path

class RoleAndPluginTests(unittest.TestCase):
    def test_role_engine_switches_known_profiles_only(self):
        engine = RoleEngine()
        self.assertEqual(engine.current()["name"], "companion")
        self.assertEqual(engine.set_role("developer")["name"], "developer")
        with self.assertRaises(ValueError):
            engine.set_role("root")
    def test_role_skill_lists_and_switches_role(self):
        skill = RoleSkill()
        self.assertTrue(skill.run("list").ok)
        self.assertTrue(skill.run("tutor").ok)
        self.assertEqual(skill.engine.current()["name"], "tutor")
        self.assertIn("permissions are unchanged", skill.run("developer").message)
    def test_plugin_loader_is_disabled_by_default_and_fails_closed(self):
        loader = PluginLoader()
        result = loader.discover()
        self.assertEqual(result["loaded"], [])
        self.assertIn("disabled", result["skipped"][0]["reason"])
    def test_registry_registers_role_skill_and_discovery_is_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            default = SkillRegistry.defaults(Path(d))
            self.assertIn("role", default.names())
            self.assertNotIn("plugin_discovery_enabled", default.names())
            enabled = SkillRegistry.defaults(Path(d), plugin_discovery_enabled=True, trusted_plugin_modules=[])
            self.assertIn("role", enabled.names())

if __name__ == "__main__":
    unittest.main()
