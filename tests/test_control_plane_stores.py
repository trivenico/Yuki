import os
import tempfile
import unittest
from pathlib import Path

from core.control_plane_stores import (
    ControlPlaneCompositionError,
    build_control_plane_stores,
)


@unittest.skipUnless(os.name == "posix", "private profile ACL validation is POSIX-only")
class ControlPlaneStoreCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "private-profile"

    def test_both_stores_share_one_fence_and_private_root(self):
        stores = build_control_plane_stores(profile_root=self.root)
        self.assertIs(stores.auth.maintenance_fence, stores.maintenance_fence)
        self.assertIs(stores.workflows.maintenance_fence, stores.maintenance_fence)
        self.assertEqual(stores.profile_root, self.root)
        self.assertEqual(Path(stores.auth.database_path).parent, self.root)
        self.assertEqual(Path(stores.workflows.database_path).parent, self.root)
        self.assertNotEqual(stores.auth.database_path, stores.workflows.database_path)
        self.assertTrue((self.root / "maintenance.lock").is_file())

    def test_store_operations_use_the_shared_os_fence(self):
        stores = build_control_plane_stores(profile_root=self.root)
        stores.auth.bootstrap_admin(
            "owner.admin", "A sufficiently long password 7!"
        )
        stores.workflows.create(
            idempotency_key="control-plane-smoke",
            workflow_name="control-plane-smoke",
            definition=[
                {
                    "name": "review",
                    "action": "emit",
                    "argument": "fenced",
                    "requires_approval": False,
                }
            ],
        )
        self.assertTrue((self.root / "maintenance.lock").is_file())

    def test_database_paths_cannot_be_overridden_in_store_options(self):
        with self.assertRaises(ValueError):
            build_control_plane_stores(
                profile_root=self.root,
                auth_options={"database_path": str(self.root / "other.sqlite3")},
            )
        with self.assertRaises(ValueError):
            build_control_plane_stores(
                profile_root=self.root,
                workflow_options={"maintenance_fence": object()},
            )

    def test_database_filenames_reject_traversal_and_separators(self):
        for name in ("../auth.sqlite3", "nested/auth.sqlite3", "nested\\\\auth.sqlite3", "bad\x00name.sqlite3"):
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    build_control_plane_stores(
                        profile_root=self.root,
                        auth_database_name=name,
                    )

    def test_same_database_filename_is_rejected(self):
        with self.assertRaises(ValueError):
            build_control_plane_stores(
                profile_root=self.root,
                auth_database_name="same.sqlite3",
                workflow_database_name="same.sqlite3",
            )

    def test_insecure_existing_profile_is_not_repaired_implicitly(self):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(ControlPlaneCompositionError):
            build_control_plane_stores(profile_root=self.root)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
