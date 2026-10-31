import unittest

from core.role_engine import RoleEngine


class RoleEngineTests(unittest.TestCase):
    def test_companion_is_default_role(self):
        engine = RoleEngine()
        self.assertEqual(engine.current()["name"], "companion")

    def test_known_roles_can_be_selected(self):
        engine = RoleEngine()
        for name in ("companion", "tutor", "interviewer", "developer", "security", "marketing"):
            with self.subTest(name=name):
                self.assertEqual(engine.set_role(name)["name"], name)

    def test_role_names_are_case_insensitive(self):
        engine = RoleEngine()
        self.assertEqual(engine.set_role("DEVELOPER")["name"], "developer")

    def test_unknown_role_does_not_change_active_role(self):
        engine = RoleEngine()
        with self.assertRaises(ValueError):
            engine.set_role("root")
        self.assertEqual(engine.current()["name"], "companion")

    def test_role_profiles_do_not_expose_permissions_or_tools(self):
        engine = RoleEngine()
        for role in engine.available():
            self.assertNotIn("tools", role)
            self.assertNotIn("permissions", role)


if __name__ == "__main__":
    unittest.main()
