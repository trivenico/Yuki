import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService
from core.device_pairing import DevicePairingService
from core.income_opportunities import SQLiteIncomeOpportunityStore
from core.inference_jobs import InferenceJobService
from core.web_host_migration import build_web_host_migration_plan


class WebHostMigrationPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.profile = self.root / "private-profile"

    def test_default_web_resources_are_inventoried_without_mutation(self):
        auth_path = self.workspace / "auth.sqlite3"
        auth = BuiltInAuthService(str(auth_path), password_iterations=100_000)
        auth.create_account("owner.user", "A sufficiently long password 9!")
        inference_path = self.workspace / "inference.sqlite3"
        pairing = DevicePairingService(inference_path)
        InferenceJobService(inference_path, pairing)
        income = SQLiteIncomeOpportunityStore(str(self.workspace / "income-opportunities.sqlite3"))
        (self.workspace / "provider-selection.json").write_text(
            '{"schema_version":1,"provider":"private-test-provider"}', encoding="utf-8"
        )

        plan = build_web_host_migration_plan(self.workspace, self.profile)

        self.assertTrue(plan.dry_run)
        self.assertFalse(plan.executable)
        self.assertEqual(
            [item.name for item in plan.sqlite_resources],
            ["auth", "inference_and_device_pairing", "income_opportunities", "conversations"],
        )
        statuses = {item.name: item.inventory.status for item in plan.sqlite_resources}
        self.assertEqual(statuses["auth"], "ready_for_review")
        self.assertEqual(statuses["inference_and_device_pairing"], "ready_for_review")
        self.assertEqual(statuses["income_opportunities"], "ready_for_review")
        self.assertEqual(plan.sqlite_resources[1].destination_status, "available_after_private_root_validation")
        self.assertEqual(plan.file_resources[0].status, "ready_for_review")
        self.assertNotIn("private-test-provider", repr(plan.to_dict()))
        self.assertFalse(self.profile.exists())

    def test_destination_collision_is_reported_without_overwrite(self):
        auth_path = self.workspace / "auth.sqlite3"
        BuiltInAuthService(str(auth_path), password_iterations=100_000)
        self.profile.mkdir()
        target = self.profile / "auth.sqlite3"
        target.write_bytes(b"keep-existing")
        plan = build_web_host_migration_plan(self.workspace, self.profile)
        auth = next(item for item in plan.sqlite_resources if item.name == "auth")
        self.assertEqual(auth.destination_status, "blocked_collision")
        self.assertEqual(target.read_bytes(), b"keep-existing")

    def test_postgres_conversation_backend_is_not_inventoried_as_local_sqlite(self):
        plan = build_web_host_migration_plan(
            self.workspace, self.profile, storage_backend="postgres"
        )
        names = [item.name for item in plan.sqlite_resources]
        self.assertNotIn("conversations", names)

    def test_overlapping_roots_are_rejected(self):
        with self.assertRaises(ValueError):
            build_web_host_migration_plan(self.workspace, self.workspace / "nested-profile")


if __name__ == "__main__":
    unittest.main()
