import importlib.util
import hashlib
import json
import tarfile
from io import BytesIO
import os
import stat
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ui.desktop.backup import BackupError, create_encrypted_backup, restore_encrypted_backup


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography optional extra is not installed")
class DesktopBackupTests(unittest.TestCase):
    def test_encrypted_backup_restores_files_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "memory.json").write_text('{"private": true}', encoding="utf-8")
            backup = root / "yuki.yukibak"
            result = create_encrypted_backup(source, backup, "correct horse battery staple")
            self.assertEqual(result["file_count"], 1)
            self.assertNotIn(b"private", backup.read_bytes())
            destination = root / "restored"
            restored = restore_encrypted_backup(backup, destination, "correct horse battery staple")
            self.assertEqual(restored["file_count"], 1)
            self.assertEqual((destination / ".yuki" / "memory.json").read_text(), '{"private": true}')
            self.assertTrue((source / "memory.json").exists())

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_restore_applies_private_permissions_to_profile_files_and_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "memory.enc.json").write_text("ciphertext")
            backup = root / "profile.yukibak"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restored"
            restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            profile = destination / ".yuki"
            self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((profile / "nested").stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((profile / "nested" / "memory.enc.json").stat().st_mode),
                0o600,
            )

    def test_restore_flushes_staged_tree_before_publication(self):
        from ui.desktop import backup as backup_module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "nested").mkdir()
            (source / "nested" / "notes.txt").write_text("durable")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restored"
            original = backup_module._fsync_staged_tree

            def verify_then_sync(stage):
                self.assertTrue((stage / ".yuki" / "nested" / "notes.txt").is_file())
                self.assertFalse(destination.exists())
                original(stage)

            with patch("ui.desktop.backup._fsync_staged_tree", side_effect=verify_then_sync) as sync:
                restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            sync.assert_called_once()
            self.assertEqual((destination / ".yuki" / "nested" / "notes.txt").read_text(), "durable")

    def test_wrong_passphrase_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "x.txt").write_text("secret")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restore"
            with self.assertRaises(BackupError):
                restore_encrypted_backup(backup, destination, "a different passphrase")
            self.assertFalse(destination.exists())

    def test_nonempty_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "x.txt").write_text("source")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restore"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep")
            with self.assertRaises(BackupError):
                restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            self.assertEqual((destination / "keep.txt").read_text(), "keep")

    def test_legacy_schema_one_backup_restores_into_yuki_home_layout(self):
        from ui.desktop import backup as backup_module
        AESGCM, Scrypt = backup_module._crypto()
        passphrase = "a sufficiently long passphrase"
        payload = b"legacy private profile data"
        manifest = json.dumps({
            "schema_version": 1,
            "product": "Yuki Desktop",
            "files": [{
                "path": "memory.json",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        archive_file = BytesIO()
        with tarfile.open(fileobj=archive_file, mode="w:") as archive:
            item = tarfile.TarInfo("memory.json")
            item.size = len(payload)
            archive.addfile(item, BytesIO(payload))
            item = tarfile.TarInfo("YUKI_BACKUP_MANIFEST.json")
            item.size = len(manifest)
            archive.addfile(item, BytesIO(manifest))
        salt = os.urandom(backup_module.SALT_BYTES)
        nonce = os.urandom(backup_module.NONCE_BYTES)
        key = backup_module._derive(passphrase, salt, Scrypt)
        encrypted = AESGCM(key).encrypt(nonce, archive_file.getvalue(), backup_module.MAGIC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup = root / "legacy.yukibak"
            backup.write_bytes(backup_module.MAGIC + salt + nonce + encrypted)
            destination = root / "restored"
            restore_encrypted_backup(backup, destination, passphrase)
            self.assertEqual(
                (destination / ".yuki" / "memory.json").read_bytes(),
                payload,
            )

    def test_portable_memory_key_round_trips_inside_encrypted_archive_only(self):
        from companion.secure_memory import EncryptedMemoryStore
        from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account

        class Keyring:
            def __init__(self):
                self.values = {}
                self.backend = type("Backend", (), {"priority": 1})()
            def get_keyring(self):
                return self.backend
            def get_password(self, service, account):
                return self.values.get((service, account))
            def set_password(self, service, account, value):
                self.values[(service, account)] = value
            def delete_password(self, service, account):
                self.values.pop((service, account), None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            profile_id = load_or_create_profile_id(source)
            source_keyring = Keyring()
            store = EncryptedMemoryStore(
                source / "memory.enc.json",
                keyring_module=source_keyring,
                keyring_account=profile_memory_keyring_account(profile_id),
            )
            store.add("portable private memory")
            encoded_key = source_keyring.get_password(
                "yuki-companion-memory", profile_memory_keyring_account(profile_id),
            ).encode("ascii")
            export = store.export_key_for_encrypted_backup(profile_id)
            backup = root / "portable.yukibak"
            create_encrypted_backup(
                source, backup, "a sufficiently long passphrase",
                memory_key_export=export,
            )
            self.assertNotIn(encoded_key, backup.read_bytes())
            destination = root / "restored"
            destination_keyring = Keyring()
            result = restore_encrypted_backup(
                backup, destination, "a sufficiently long passphrase",
                keyring_module=destination_keyring, import_memory_key=True,
            )
            self.assertTrue(result["memory_key_imported"])
            self.assertFalse((destination / ".yuki" / "memory_key_export.json").exists())
            restored_id = load_or_create_profile_id(destination / ".yuki")
            restored = EncryptedMemoryStore(
                destination / ".yuki" / "memory.enc.json",
                keyring_module=destination_keyring,
                keyring_account=profile_memory_keyring_account(restored_id),
            )
            self.assertEqual(restored.list()[0]["text"], "portable private memory")

    def test_failed_profile_publication_rolls_back_new_keyring_entry(self):
        from companion.secure_memory import EncryptedMemoryStore
        from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account

        class Keyring:
            def __init__(self):
                self.values = {}
                self.backend = type("Backend", (), {"priority": 1})()
            def get_keyring(self):
                return self.backend
            def get_password(self, service, account):
                return self.values.get((service, account))
            def set_password(self, service, account, value):
                self.values[(service, account)] = value
            def delete_password(self, service, account):
                self.values.pop((service, account), None)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            profile_id = load_or_create_profile_id(source)
            source_keyring = Keyring()
            store = EncryptedMemoryStore(
                source / "memory.enc.json", keyring_module=source_keyring,
                keyring_account=profile_memory_keyring_account(profile_id),
            )
            store.add("rollback test")
            backup = root / "portable.yukibak"
            create_encrypted_backup(
                source, backup, "a sufficiently long passphrase",
                memory_key_export=store.export_key_for_encrypted_backup(profile_id),
            )
            destination_keyring = Keyring()
            destination = root / "restored"
            with patch.object(Path, "rename", side_effect=OSError("simulated publication failure")):
                with self.assertRaisesRegex(BackupError, "invalid"):
                    restore_encrypted_backup(
                        backup, destination, "a sufficiently long passphrase",
                        keyring_module=destination_keyring, import_memory_key=True,
                    )
            self.assertEqual(destination_keyring.values, {})
            self.assertFalse(destination.exists())

    def test_invalid_memory_ciphertext_never_installs_portable_key(self):
        from companion.secure_memory import EncryptedMemoryStore
        from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account

        class Keyring:
            def __init__(self):
                self.values = {}
                self.backend = type("Backend", (), {"priority": 1})()
            def get_keyring(self):
                return self.backend
            def get_password(self, service, account):
                return self.values.get((service, account))
            def set_password(self, service, account, value):
                self.values[(service, account)] = value

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            profile_id = load_or_create_profile_id(source)
            source_keyring = Keyring()
            store = EncryptedMemoryStore(
                source / "memory.enc.json", keyring_module=source_keyring,
                keyring_account=profile_memory_keyring_account(profile_id),
            )
            store.add("integrity protected")
            export = store.export_key_for_encrypted_backup(profile_id)
            envelope = json.loads((source / "memory.enc.json").read_text())
            envelope["ciphertext"] = "eHh4eHh4eHh4eHh4eHh4eA=="
            (source / "memory.enc.json").write_text(json.dumps(envelope))
            backup = root / "tampered.yukibak"
            create_encrypted_backup(
                source, backup, "a sufficiently long passphrase",
                memory_key_export=export,
            )
            destination = root / "restored"
            destination_keyring = Keyring()
            with self.assertRaisesRegex(BackupError, "Portable memory key could not be safely installed"):
                restore_encrypted_backup(
                    backup, destination, "a sufficiently long passphrase",
                    keyring_module=destination_keyring, import_memory_key=True,
                )
            self.assertEqual(destination_keyring.values, {})
            self.assertFalse(destination.exists())

    def test_portable_memory_key_restore_requires_explicit_import(self):
        from companion.secure_memory import EncryptedMemoryStore
        from core.profile_identity import load_or_create_profile_id, profile_memory_keyring_account

        class Keyring:
            def __init__(self):
                self.values = {}
                self.backend = type("Backend", (), {"priority": 1})()
            def get_keyring(self):
                return self.backend
            def get_password(self, service, account):
                return self.values.get((service, account))
            def set_password(self, service, account, value):
                self.values[(service, account)] = value

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            profile_id = load_or_create_profile_id(source)
            source_keyring = Keyring()
            store = EncryptedMemoryStore(
                source / "memory.enc.json", keyring_module=source_keyring,
                keyring_account=profile_memory_keyring_account(profile_id),
            )
            store.add("private")
            backup = root / "portable.yukibak"
            create_encrypted_backup(
                source, backup, "a sufficiently long passphrase",
                memory_key_export=store.export_key_for_encrypted_backup(profile_id),
            )
            destination = root / "restored"
            with self.assertRaisesRegex(BackupError, "Explicitly enable memory-key import"):
                restore_encrypted_backup(
                    backup, destination, "a sufficiently long passphrase",
                    keyring_module=Keyring(), import_memory_key=False,
                )
            self.assertFalse(destination.exists())

    def test_restore_reconstructs_yuki_home_profile_and_workspace_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            (source / "owner-profile.json").write_bytes(b"opaque-profile-data")
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "notes.json").write_text('{"saved": true}', encoding="utf-8")
            backup = root / "profile.yukibak"
            create_encrypted_backup(
                source, backup, "a sufficiently long passphrase",
                additional_files={"workspace/notes.json": workspace / "notes.json"},
            )
            destination = root / "restored-home"
            restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            self.assertEqual(
                (destination / ".yuki" / "owner-profile.json").read_bytes(),
                b"opaque-profile-data",
            )
            self.assertEqual(
                (destination / "workspace" / "notes.json").read_text(encoding="utf-8"),
                '{"saved": true}',
            )

    def test_additional_workspace_database_is_included_and_snapshotted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            (source / "auth.txt").write_text("owner profile")
            workspace = root / "workspace"
            workspace.mkdir()
            database_path = workspace / "conversations.sqlite3"
            (workspace / "notes.json").write_text('{"workspace": true}', encoding="utf-8")
            writer = sqlite3.connect(database_path)
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
            writer.execute("INSERT INTO messages(body) VALUES ('durable conversation')")
            writer.commit()
            backup = root / "profile.yukibak"
            try:
                create_encrypted_backup(
                    source, backup, "a sufficiently long passphrase",
                    additional_roots={"workspace": workspace},
                )
            finally:
                writer.close()
            destination = root / "restored"
            result = restore_encrypted_backup(
                backup, destination, "a sufficiently long passphrase",
            )
            self.assertEqual(result["file_count"], 3)
            self.assertEqual(
                (destination / "workspace" / "notes.json").read_text(encoding="utf-8"),
                '{"workspace": true}',
            )
            restored = sqlite3.connect(destination / "workspace" / "conversations.sqlite3")
            try:
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    restored.execute("SELECT body FROM messages").fetchone()[0],
                    "durable conversation",
                )
            finally:
                restored.close()

    def test_additional_workspace_root_rejects_symlink_and_backup_output_inside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "notes.txt").write_text("private")
            with self.assertRaisesRegex(BackupError, "outside every source data directory"):
                create_encrypted_backup(
                    source, workspace / "backup.yukibak",
                    "a sufficiently long passphrase",
                    additional_roots={"workspace": workspace},
                )
            link = root / "workspace-link"
            try:
                link.symlink_to(workspace, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(BackupError, "symbolic link"):
                create_encrypted_backup(
                    source, root / "backup.yukibak",
                    "a sufficiently long passphrase",
                    additional_roots={"workspace": link},
                )

    def test_additional_backup_file_cannot_shadow_reserved_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            extra = root / "manifest.json"
            extra.write_text("not the archive manifest")
            with self.assertRaisesRegex(BackupError, "reserved backup manifest"):
                create_encrypted_backup(
                    source, root / "backup", "a sufficiently long passphrase",
                    additional_files={"yuki_backup_manifest.json": extra},
                )
            self.assertFalse((root / "backup").exists())

    def test_additional_backup_file_rejects_unsafe_or_colliding_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "desktop"
            source.mkdir()
            (source / "config.json").write_text("{}")
            external = root / "extra.txt"
            external.write_text("extra")
            with self.assertRaises(BackupError):
                create_encrypted_backup(
                    source, root / "unsafe.yukibak", "a sufficiently long passphrase",
                    additional_files={"../escape.txt": external},
                )
            with self.assertRaises(BackupError):
                create_encrypted_backup(
                    source, root / "collision.yukibak", "a sufficiently long passphrase",
                    additional_files={".yuki/CONFIG.JSON": external},
                )
            self.assertFalse((root / "unsafe.yukibak").exists())
            self.assertFalse((root / "collision.yukibak").exists())

    def test_sqlite_is_snapshotted_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            db = sqlite3.connect(source / "tasks.sqlite3")
            db.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("INSERT INTO tasks(name) VALUES ('preserved')")
            db.commit()
            db.close()
            # A stale WAL sidecar must not be restored beside the SQLite backup snapshot.
            (source / "tasks.sqlite3-wal").write_bytes(b"stale-wal")
            backup = root / "backup"
            create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            destination = root / "restore"
            restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            restored = sqlite3.connect(destination / ".yuki" / "tasks.sqlite3")
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT name FROM tasks").fetchone()[0], "preserved")
            restored.close()
            self.assertFalse((destination / ".yuki" / "tasks.sqlite3-wal").exists())

    def test_orphan_sqlite_sidecar_is_rejected_instead_of_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            (source / "orphan.sqlite3-wal").write_bytes(b"unattached wal data")
            with self.assertRaisesRegex(BackupError, "orphan SQLite sidecar"):
                create_encrypted_backup(
                    source, root / "backup", "a sufficiently long passphrase",
                )
            self.assertFalse((root / "backup").exists())

    def test_sqlite_wal_committed_data_is_included_in_consistent_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            database_path = source / "live.sqlite3"
            writer = sqlite3.connect(database_path)
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)")
            writer.commit()
            writer.execute("INSERT INTO events(value) VALUES ('committed-in-wal')")
            writer.commit()
            wal_path = Path(str(database_path) + "-wal")
            self.assertTrue(wal_path.exists())
            self.assertGreater(wal_path.stat().st_size, 0)

            backup = root / "live-backup.yukibak"
            try:
                create_encrypted_backup(source, backup, "a sufficiently long passphrase")
            finally:
                writer.close()

            destination = root / "restored"
            restore_encrypted_backup(backup, destination, "a sufficiently long passphrase")
            restored = sqlite3.connect(destination / ".yuki" / "live.sqlite3")
            try:
                self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(
                    restored.execute("SELECT value FROM events").fetchone()[0],
                    "committed-in-wal",
                )
            finally:
                restored.close()
            self.assertFalse((destination / ".yuki" / "live.sqlite3-wal").exists())
            self.assertFalse((destination / ".yuki" / "live.sqlite3-shm").exists())

    def test_rejects_cross_platform_archive_path_traversal(self):
        from ui.desktop.backup import _safe_archive_path
        for name in ("../escape", "..\\\\escape", "C:/Windows/system.ini", "/absolute/path", "folder/./file", "CON", "nul.txt", "LPT1.log", "trailing. ", "name:stream", "bad?.txt"):
            with self.subTest(name=name):
                with self.assertRaises(BackupError):
                    _safe_archive_path(name)

    def test_rejects_case_colliding_archive_paths(self):
        from ui.desktop.backup import _portable_archive_key, _safe_archive_path
        first = _portable_archive_key(_safe_archive_path("Config.json"))
        second = _portable_archive_key(_safe_archive_path("config.json"))
        self.assertEqual(first, second)

    def test_rejects_symbolic_link_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data"
            source.mkdir()
            link = root / "link"
            try:
                link.symlink_to(source, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(BackupError):
                create_encrypted_backup(link, root / "backup", "a sufficiently long passphrase")


if __name__ == "__main__":
    unittest.main()
