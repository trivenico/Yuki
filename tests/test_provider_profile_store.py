import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from core.sqlite_file_security import SQLitePathSecurityError
from providers.profile_store import OSKeyringVault, ProviderProfileStore


class MemoryVault:
    def __init__(self):
        self.values = {}
    def set_secret(self, reference, secret):
        self.values[reference] = secret
    def get_secret(self, reference):
        return self.values.get(reference)
    def delete_secret(self, reference):
        self.values.pop(reference, None)


class ProviderProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = MemoryVault()
        self.store = ProviderProfileStore(self.root / "profiles.sqlite", self.vault)
        self.addCleanup(self.store.close)

    def save(self, **overrides):
        values = dict(owner_id="owner:alice", name="gateway",
                      base_url="https://gateway.example.test/v1",
                      model="vendor/model", allowed_host="gateway.example.test",
                      api_key="secret-value")
        values.update(overrides)
        return self.store.save(**values)

    def test_os_keyring_rejects_positive_priority_plaintext_backend(self):
        class PlaintextBackend:
            priority = 1
        PlaintextBackend.__module__ = "keyrings.alt.file"
        class FakeKeyring:
            @staticmethod
            def get_keyring():
                return PlaintextBackend()
        with self.assertRaisesRegex(RuntimeError, "reviewed native"):
            OSKeyringVault(keyring_module=FakeKeyring)

    def test_os_keyring_accepts_reviewed_native_secret_service_backend(self):
        class Keyring:
            priority = 5
        Keyring.__module__ = "keyring.backends.SecretService"
        class FakeKeyring:
            @staticmethod
            def get_keyring():
                return Keyring()
        vault = OSKeyringVault(keyring_module=FakeKeyring)
        self.assertIsNotNone(vault)

    def test_profile_metadata_persists_without_secret_material(self):
        profile = self.save()
        reopened = ProviderProfileStore(self.root / "profiles.sqlite", self.vault)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.list_profiles("owner:alice")[0].name, "gateway")
        self.assertNotIn("secret-value", repr(profile))
        with closing(sqlite3.connect(self.root / "profiles.sqlite")) as db:
            self.assertNotIn("secret-value", repr(db.execute(
                "SELECT * FROM provider_profiles").fetchall()))
        self.assertEqual(reopened.resolve_for_runtime("owner:alice", "gateway")["api_key"],
                         "secret-value")

    def test_accepts_url_safe_owner_ids_with_leading_punctuation(self):
        for owner_id in ("_owner", "-owner"):
            with self.subTest(owner_id=owner_id):
                self.save(owner_id=owner_id, name="gateway")
                self.assertEqual(self.store.list_profiles(owner_id)[0].name, "gateway")
                self.store.select_profile(owner_id, "gateway")
                self.assertEqual(self.store.selected_profile(owner_id), "gateway")

    def test_profiles_are_owner_scoped(self):
        self.save()
        self.assertEqual(self.store.list_profiles("owner:bob"), ())
        with self.assertRaises(KeyError):
            self.store.resolve_for_runtime("owner:bob", "gateway")
        self.assertFalse(self.store.delete("owner:bob", "gateway"))

    def test_rotation_replaces_vault_secret_and_preserves_creation_time(self):
        first = self.save()
        old_refs = set(self.vault.values)
        second = self.save(api_key="rotated-secret")
        self.assertEqual(first.created_at, second.created_at)
        self.assertEqual(self.vault.values, {next(iter(self.vault.values)): "rotated-secret"})
        self.assertNotEqual(old_refs, set(self.vault.values))
        self.assertEqual(self.store.resolve_for_runtime("owner:alice", "gateway")["api_key"],
                         "rotated-secret")

    def test_selected_profile_is_owner_scoped_and_cleared_on_delete(self):
        self.save()
        self.store.select_profile("owner:alice", "gateway")
        self.assertEqual(self.store.selected_profile("owner:alice"), "gateway")
        self.assertIsNone(self.store.selected_profile("owner:bob"))
        self.assertTrue(self.store.delete("owner:alice", "gateway"))
        self.assertIsNone(self.store.selected_profile("owner:alice"))

    def test_delete_removes_profile_and_credential(self):
        self.save()
        self.assertTrue(self.store.delete("owner:alice", "gateway"))
        self.assertEqual(self.store.list_profiles("owner:alice"), ())
        self.assertEqual(self.vault.values, {})

    def test_rejects_non_https_host_mismatch_and_nonstandard_port(self):
        for kwargs in (
            {"base_url": "http://gateway.example.test/v1"},
            {"base_url": "https://attacker.example.test/v1"},
            {"base_url": "https://gateway.example.test:8443/v1"},
            {"base_url": "https://gateway.example.test/v1?token=x"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.save(**kwargs)

    def test_rejects_malformed_owner_and_profile_identifiers(self):
        for field, value in (("owner_id", "../alice"), ("name", "Gateway"), ("name", "a b")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.save(**{field: value})

    def test_missing_vault_secret_fails_closed(self):
        self.save()
        self.vault.values.clear()
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.store.resolve_for_runtime("owner:alice", "gateway")

    def test_delete_fails_closed_when_vault_is_unavailable(self):
        self.save()

        class UnavailableVault:
            def set_secret(self, reference, secret):
                raise RuntimeError("locked")
            def get_secret(self, reference):
                raise RuntimeError("locked")
            def delete_secret(self, reference):
                raise RuntimeError("locked")

        store = ProviderProfileStore(self.root / "profiles.sqlite", UnavailableVault())
        self.addCleanup(store.close)
        with self.assertRaisesRegex(RuntimeError, "profile was not removed"):
            store.delete("owner:alice", "gateway")
        self.assertEqual(len(store.list_profiles("owner:alice")), 1)

    def test_closed_store_rejects_operations(self):
        self.store.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.store.list_profiles("owner:alice")


    @unittest.skipUnless(os.name == "posix", "POSIX file modes required")
    def test_database_file_is_private(self):
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_database_symlink_is_rejected(self):
        self.store.close()
        target = self.root / "target.sqlite"
        target.touch()
        link = self.root / "linked.sqlite"
        link.symlink_to(target)
        with self.assertRaises(SQLitePathSecurityError):
            ProviderProfileStore(link, self.vault)

    @unittest.skipUnless(hasattr(os, "link"), "hard links unavailable")
    def test_database_hard_link_is_rejected(self):
        self.store.close()
        alias = self.root / "alias.sqlite"
        os.link(self.store.path, alias)
        with self.assertRaisesRegex(ValueError, "hard_links"):
            ProviderProfileStore(alias, self.vault)


if __name__ == "__main__":
    unittest.main()
