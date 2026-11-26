import os
import tempfile
import unittest
from pathlib import Path

from core.private_profile_migration import build_private_profile_migration_plan


class PrivateProfileMigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.profile = self.root / "profile"

    def test_empty_inventory_is_dry_run_and_non_executable(self):
        plan = build_private_profile_migration_plan(self.workspace, self.profile)
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.executable)
        self.assertEqual([item.status for item in plan.items], ["missing", "missing"])
        self.assertFalse(self.profile.exists())

    def test_owner_identity_is_metadata_only_and_ready_for_review(self):
        identity = self.workspace / "local-owner-id"
        identity.write_text("local-" + "x" * 32 + "\n", encoding="ascii")
        plan = build_private_profile_migration_plan(self.workspace, self.profile)
        item = plan.items[0]
        self.assertEqual(item.status, "ready_for_review")
        self.assertNotIn("local-" + "x" * 32, repr(plan.to_dict()))
        self.assertEqual(item.metadata.kind, "regular_file")
        self.assertFalse(self.profile.exists())

    def test_destination_collision_never_overwrites(self):
        (self.workspace / "local-owner-id").write_text("legacy", encoding="ascii")
        self.profile.mkdir()
        destination = self.profile / "local-owner-id"
        destination.write_text("new", encoding="ascii")
        plan = build_private_profile_migration_plan(self.workspace, self.profile)
        self.assertEqual(plan.items[0].status, "blocked_collision")
        self.assertEqual(destination.read_text(encoding="ascii"), "new")

    def test_symlink_source_is_blocked(self):
        actual = self.root / "actual"
        actual.write_text("do not read", encoding="ascii")
        (self.workspace / "local-owner-id").symlink_to(actual)
        plan = build_private_profile_migration_plan(self.workspace, self.profile)
        self.assertEqual(plan.items[0].status, "blocked_unsafe_source")
        self.assertEqual(plan.items[0].metadata.kind, "symlink")

    def test_hard_link_source_is_blocked_on_posix(self):
        if os.name != "posix":
            self.skipTest("hard-link count semantics are POSIX-specific")
        source = self.workspace / "local-owner-id"
        source.write_text("legacy", encoding="ascii")
        os.link(source, self.root / "second-link")
        plan = build_private_profile_migration_plan(self.workspace, self.profile)
        self.assertEqual(plan.items[0].status, "blocked_unsafe_source")
        self.assertEqual(plan.items[0].metadata.link_count, 2)

    def test_sqlite_inventory_includes_wal_and_shm_without_copy_authorization(self):
        database = self.workspace / "conversations.sqlite3"
        database.write_bytes(b"not inspected")
        (self.workspace / "conversations.sqlite3-wal").write_bytes(b"wal")
        (self.workspace / "conversations.sqlite3-shm").write_bytes(b"shm")
        plan = build_private_profile_migration_plan(self.workspace, self.profile)
        item = plan.items[1]
        self.assertEqual(item.status, "requires_consistent_sqlite_backup")
        self.assertEqual(dict(item.sidecars)["-wal"].exists, True)
        self.assertEqual(dict(item.sidecars)["-shm"].exists, True)
        self.assertFalse(plan.executable)

    def test_rejects_overlapping_roots(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            build_private_profile_migration_plan(self.workspace, self.workspace / "profile")

    def test_rejects_workspace_nested_inside_profile_root(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            build_private_profile_migration_plan(self.profile, self.profile / "workspace")

    def test_report_is_repeatable_and_does_not_create_destination(self):
        first = build_private_profile_migration_plan(self.workspace, self.profile).to_dict()
        second = build_private_profile_migration_plan(self.workspace, self.profile).to_dict()
        self.assertEqual(first, second)
        self.assertFalse(self.profile.exists())


if __name__ == "__main__":
    unittest.main()
