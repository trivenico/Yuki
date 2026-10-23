import os
import tempfile
import unittest
from pathlib import Path

from scripts.private_deployment_preflight import inspect_environment


class PrivateDeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "auth.sqlite3"
        self.env = {
            "YUKI_DEPLOYMENT_MODE": "private",
            "YUKI_BIND_HOST": "127.0.0.1",
            "YUKI_AUTH_DB": str(self.db),
            "YUKI_PRIVATE_ACCESS": "wireguard",
            "YUKI_PUBLIC_INGRESS": "false",
            "YUKI_SECRET_SOURCE": "systemd-credentials",
            "YUKI_BACKUP_VERIFIED": "true",
            "YUKI_OWNER_IDENTITY": "owner@example.invalid",
        }

    def test_accepts_private_profile_with_restricted_parent(self):
        if os.name != "nt":
            self.root.chmod(0o700)
        self.assertTrue(inspect_environment(self.env)["ok"])

    def test_fails_closed_without_private_access_and_owner_identity(self):
        self.env.pop("YUKI_PRIVATE_ACCESS")
        self.env.pop("YUKI_OWNER_IDENTITY")
        result = inspect_environment(self.env)
        self.assertFalse(result["ok"])
        self.assertTrue(any("YUKI_PRIVATE_ACCESS" in item for item in result["errors"]))
        self.assertTrue(any("YUKI_OWNER_IDENTITY" in item for item in result["errors"]))

    def test_rejects_public_bind_and_public_ingress(self):
        self.env["YUKI_BIND_HOST"] = "0.0.0.0"
        self.env["YUKI_PUBLIC_INGRESS"] = "true"
        result = inspect_environment(self.env)
        self.assertFalse(result["ok"])
        self.assertTrue(any("loopback-only" in item for item in result["errors"]))
        self.assertTrue(any("Public ingress" in item for item in result["errors"]))

    def test_requires_absolute_auth_database_path(self):
        self.env["YUKI_AUTH_DB"] = "relative/auth.sqlite3"
        self.assertFalse(inspect_environment(self.env)["ok"])

    def test_rejects_broad_database_permissions(self):
        self.root.chmod(0o755)
        self.db.write_text("test")
        self.db.chmod(0o644)
        result = inspect_environment(self.env, platform="linux")
        self.assertFalse(result["ok"])
        self.assertTrue(any("permissions are too broad" in item for item in result["errors"]))


    def test_rejects_symlinked_auth_database(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        target = self.root / "target.sqlite3"
        target.write_text("test")
        self.db.symlink_to(target)
        result = inspect_environment(self.env, platform="linux")
        self.assertFalse(result["ok"])
        self.assertTrue(any("symbolic link" in item for item in result["errors"]))

    def test_checks_parent_permissions_even_when_database_exists(self):
        self.db.write_text("test")
        self.db.chmod(0o600)
        self.root.chmod(0o755)
        result = inspect_environment(self.env, platform="linux")
        self.assertFalse(result["ok"])
        self.assertTrue(any("directory permissions are too broad" in item for item in result["errors"]))

    def test_windows_acl_check_is_explicitly_a_warning(self):
        result = inspect_environment(self.env, platform="win32")
        self.assertTrue(result["ok"])
        self.assertTrue(any("Windows ACLs" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
