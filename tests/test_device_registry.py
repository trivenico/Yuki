import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cloud.device_registry import DeviceCredentialError, DeviceCredentialStore


class DeviceCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "devices.sqlite3")
        self.store = DeviceCredentialStore(self.path)

    def test_issue_returns_one_time_secret_and_database_stores_only_digest(self):
        issued = self.store.issue(user_id="user-1", device_name="Yuki Desktop")
        self.assertTrue(issued.token.startswith("yuki_dev_"))
        with sqlite3.connect(self.path) as db:
            stored = db.execute("SELECT token_hash FROM gateway_devices").fetchone()[0]
        self.assertNotEqual(stored, issued.token)
        self.assertEqual(len(stored), 64)
        principal = self.store.authenticate(issued.token)
        self.assertEqual(principal.device_id, issued.device_id)
        self.assertEqual(principal.user_id, "user-1")
        self.assertIn("chat:write", principal.scopes)

    def test_revocation_is_immediate_and_owner_scoped(self):
        issued = self.store.issue(user_id="owner-a", device_name="Laptop")
        self.assertFalse(self.store.revoke(user_id="owner-b", device_id=issued.device_id))
        self.assertTrue(self.store.authenticate(issued.token))
        self.assertTrue(self.store.revoke(user_id="owner-a", device_id=issued.device_id))
        with self.assertRaises(DeviceCredentialError):
            self.store.authenticate(issued.token)
        self.assertFalse(self.store.revoke(user_id="owner-a", device_id=issued.device_id))

    def test_scope_validation_rejects_privilege_escalation(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.store.issue(
                user_id="user-1",
                device_name="Laptop",
                scopes=("admin:*", "users:manage"),
            )
        issued = self.store.issue(
            user_id="user-1",
            device_name="Read-only device",
            scopes=("profile:read", "tasks:read"),
        )
        self.assertEqual(
            self.store.authenticate(issued.token).scopes,
            frozenset({"profile:read", "tasks:read"}),
        )

    def test_list_devices_never_exposes_token_or_hash(self):
        issued = self.store.issue(user_id="user-1", device_name="Desktop")
        rows = self.store.list_for_user(user_id="user-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["device_id"], issued.device_id)
        self.assertNotIn("token", rows[0])
        self.assertNotIn("token_hash", rows[0])
        self.assertEqual(self.store.list_for_user(user_id="other-user"), [])

    def test_invalid_credentials_and_inputs_fail_closed(self):
        for token in (None, "", "not-a-token", "yuki_dev_short"):
            with self.assertRaises(DeviceCredentialError):
                self.store.authenticate(token)
        with self.assertRaises(ValueError):
            self.store.issue(user_id="", device_name="Laptop")
        with self.assertRaises(ValueError):
            self.store.issue(user_id="user-1", device_name=" ")


if __name__ == "__main__":
    unittest.main()
