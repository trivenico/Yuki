import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.workflow_store import WorkflowRunStore
from core.auth import BuiltInAuthService
from core.control_plane_migration import plan_control_plane_migration


class ControlPlaneMigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth_path = self.root / "auth.sqlite3"
        self.workflow_path = self.root / "tasks.sqlite3"
        self.destination = self.root / "control.sqlite3"
        self.auth = BuiltInAuthService(str(self.auth_path), password_iterations=100_000)
        self.auth.create_account("owner.user", "A sufficiently long password 9!")
        self.workflow = WorkflowRunStore(str(self.workflow_path))
        self.workflow.create(
            idempotency_key="preflight-task",
            workflow_name="review",
            definition=[{"name": "inspect", "action": "emit", "argument": "metadata", "requires_approval": True}],
        )

    def test_preflight_inventories_both_stores_without_creating_destination(self):
        plan = plan_control_plane_migration(
            self.auth_path, self.workflow_path, self.destination,
        )
        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.executable)
        self.assertEqual(plan.auth.status, "ready_for_review")
        self.assertEqual(plan.workflow.status, "ready_for_review")
        self.assertEqual(plan.destination_status, "available")
        self.assertIn(("auth_users", 1), plan.auth.tables)
        self.assertIn(("workflow_runs", 1), plan.workflow.tables)
        self.assertFalse(self.destination.exists())
        self.assertIn(
            "dry_run_only_no_files_created_copied_modified_or_deleted",
            plan.warnings,
        )

    def test_missing_required_table_fails_closed(self):
        incomplete = self.root / "incomplete.sqlite3"
        with sqlite3.connect(incomplete) as db:
            db.execute("CREATE TABLE auth_users (user_id TEXT PRIMARY KEY)")
        plan = plan_control_plane_migration(
            incomplete, self.workflow_path, self.destination,
        )
        self.assertEqual(plan.auth.status, "blocked_schema")
        self.assertTrue(plan.auth.missing_tables)
        self.assertFalse(plan.executable)

    def test_existing_destination_is_never_overwritten(self):
        self.destination.write_bytes(b"preserve-existing-destination")
        before = self.destination.read_bytes()
        plan = plan_control_plane_migration(
            self.auth_path, self.workflow_path, self.destination,
        )
        self.assertEqual(plan.destination_status, "blocked_destination_exists")
        self.assertEqual(self.destination.read_bytes(), before)
        self.assertFalse(plan.executable)

    def test_symlinked_source_parent_is_rejected_before_leaf_inspection(self):
        alias = self.root / "source-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        plan = plan_control_plane_migration(
            alias / "auth.sqlite3",
            self.workflow_path,
            self.destination,
        )
        self.assertEqual(plan.auth.status, "blocked_unsafe_path")
        self.assertEqual(plan.auth.reason, "parent_component_is_symlink")
        self.assertFalse(plan.executable)

    def test_source_and_destination_paths_must_be_distinct(self):
        with self.assertRaises(ValueError):
            plan_control_plane_migration(
                self.auth_path, self.workflow_path, self.auth_path,
            )

    def test_report_is_json_serializable_and_contains_no_row_values(self):
        import json
        plan = plan_control_plane_migration(
            self.auth_path, self.workflow_path, self.destination,
        )
        rendered = json.dumps(plan.to_dict(), sort_keys=True)
        self.assertNotIn("owner.user", rendered)
        self.assertNotIn("A sufficiently long password", rendered)
        self.assertIn('"dry_run": true', rendered)


if __name__ == "__main__":
    unittest.main()
