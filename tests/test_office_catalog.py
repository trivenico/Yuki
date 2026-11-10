import unittest
from core.office_catalog import build_role_brief, council_members, list_roles, plan_review_team

class OfficeCatalogTests(unittest.TestCase):
    def test_four_councils_have_32_unique_profiles(self):
        members = council_members()
        self.assertEqual(len(members), 32)
        self.assertEqual(len({member.name for member in members}), 32)
        self.assertEqual({member.council for member in members}, {"genius", "business", "ceo", "developer"})

    def test_enterprise_roles_are_unique_and_cover_core_functions(self):
        roles = list_roles()
        names = [role.name for role in roles]
        self.assertGreaterEqual(len(roles), 25)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("CEO", names)
        self.assertIn("QA Tester", names)
        self.assertIn("Senior Cybersecurity Engineer", names)

    def test_role_brief_does_not_grant_permissions(self):
        brief = build_role_brief("DevOps Engineer", "Review deployment rollback.")
        self.assertIn("grants no tool", brief["permission_note"])
        self.assertEqual(brief["department"], "platform")

    def test_unknown_role_empty_task_and_long_task_fail_closed(self):
        with self.assertRaises(ValueError): build_role_brief("Unknown Role", "Do work")
        with self.assertRaises(ValueError): build_role_brief("CEO", "  ")
        with self.assertRaises(ValueError): build_role_brief("CEO", "x" * 4001)

    def test_review_team_is_metadata_only(self):
        team = plan_review_team("Review MVP risks.", ["Software Architect", "QA Tester"])
        self.assertEqual(len(team), 2)
        self.assertTrue(all("permission_note" in item for item in team))

    def test_roles_must_be_iterable_not_string(self):
        with self.assertRaises(TypeError): plan_review_team("Review", "QA Tester")

if __name__ == "__main__": unittest.main()