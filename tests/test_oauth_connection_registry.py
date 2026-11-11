import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry


class OAuthConnectionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "oauth-connections.sqlite3")
        self.registry = SQLiteOAuthConnectionRegistry(self.path, clock=lambda: 1234)

    def tearDown(self):
        self.temp.cleanup()

    def test_register_list_and_get_non_secret_metadata(self):
        record = self.registry.register(
            owner_id="owner-a", provider_id="provider-a", account_id="account-a",
            scopes=("read", "profile"),
        )
        self.assertEqual(record.connected_at, 1234)
        self.assertEqual(record.scopes, ("profile", "read"))
        self.assertEqual(self.registry.get(
            owner_id="owner-a", provider_id="provider-a", account_id="account-a"
        ), record)
        self.assertEqual(self.registry.list_for_owner(owner_id="owner-a"), [record])

    def test_owner_scope_prevents_cross_owner_reads_and_deletes(self):
        self.registry.register(owner_id="owner-a", provider_id="p", account_id="a", scopes=())
        self.assertIsNone(self.registry.get(owner_id="owner-b", provider_id="p", account_id="a"))
        self.assertFalse(self.registry.remove(owner_id="owner-b", provider_id="p", account_id="a"))
        self.assertEqual(len(self.registry.list_for_owner(owner_id="owner-a")), 1)

    def test_re_registration_updates_metadata_without_duplicate(self):
        self.registry.register(owner_id="o", provider_id="p", account_id="a", scopes=("read",))
        updated = self.registry.register(owner_id="o", provider_id="p", account_id="a", scopes=("write",))
        self.assertEqual(updated.scopes, ("write",))
        self.assertEqual(len(self.registry.list_for_owner(owner_id="o")), 1)

    def test_creates_missing_database_parent_directory(self):
        nested = str(Path(self.temp.name) / "nested" / "registry.sqlite3")
        registry = SQLiteOAuthConnectionRegistry(nested, clock=lambda: 1234)
        self.assertTrue(Path(nested).parent.is_dir())
        self.assertEqual(registry.list_for_owner(owner_id="owner"), [])

    def test_rejects_invalid_identifiers_and_scopes(self):
        with self.assertRaises(ValueError):
            self.registry.register(owner_id="../other", provider_id="p", account_id="a", scopes=())
        with self.assertRaises(ValueError):
            self.registry.register(owner_id="o", provider_id="p", account_id="a", scopes=("bad scope",))


if __name__ == "__main__":
    unittest.main()
