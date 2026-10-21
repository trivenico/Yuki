import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from core.plugin_loader import PluginLoader
from core.role_engine import RoleEngine
from skills.role_skill import RoleSkill
from skills.registry import SkillRegistry

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

    def test_plugin_loader_rejects_wildcard_trust(self):
        with self.assertRaisesRegex(ValueError, "Wildcard plugin trust is prohibited"):
            PluginLoader(trusted_modules=["*"], enabled=True)

    def test_plugin_loader_is_disabled_by_default_and_fails_closed(self):
        loader = PluginLoader()
        result = loader.discover()
        self.assertEqual(result["loaded"], [])
        self.assertIn("disabled", result["skipped"][0]["reason"])

    def test_plugin_discovery_loads_only_trusted_module(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            package = root / "fixture_skills"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "trusted.py").write_text(
                "from skills.base import Skill, SkillResult\n"
                "class FixtureSkill(Skill):\n"
                " name = 'fixture_plugin'\n"
                " description = 'test plugin'\n"
                " requires_approval = False\n"
                " def run(self, argument=''):\n"
                "  return SkillResult(True, 'ok', {'argument': argument})\n",
                encoding="utf-8",
            )
            (package / "untrusted.py").write_text(
                "raise RuntimeError('must not import untrusted module')\n",
                encoding="utf-8",
            )
            sys.path.insert(0, d)
            try:
                importlib.invalidate_caches()
                loader = PluginLoader("fixture_skills", trusted_modules=["trusted"], enabled=True)
                result = loader.discover()
                self.assertEqual([s.name for s in result["loaded"]], ["fixture_plugin"])
                self.assertTrue(any(x["module"] == "untrusted" for x in result["skipped"]))
            finally:
                sys.path.remove(d)
                for name in list(sys.modules):
                    if name == "fixture_skills" or name.startswith("fixture_skills."):
                        del sys.modules[name]
                importlib.invalidate_caches()

    def test_registry_registers_role_skill_and_discovery_is_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            default = SkillRegistry.defaults(Path(d))
            self.assertIn("role", default.names())
            enabled = SkillRegistry.defaults(Path(d), plugin_discovery_enabled=True, trusted_plugin_modules=[])
            self.assertIn("role", enabled.names())

if __name__ == "__main__":
    unittest.main()
