import os
import stat
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from automation.workflow_store import WorkflowRunStore
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from core.pending_approvals import SQLitePendingApprovalStore
from core.sqlite_file_security import (
    SQLitePathSecurityError,
    prepare_private_sqlite_path,
    SQLiteIntegrityError,
    verify_sqlite_integrity,
)


class _TestCipher:
    def encrypt(self, plaintext, *, associated_data):
        return "test-key", plaintext

    def decrypt(self, key_id, ciphertext, *, associated_data):
        return ciphertext


class SQLiteFileSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_new_database_is_owner_only(self):
        path = Path(prepare_private_sqlite_path(self.root / "private.sqlite3"))
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_existing_permissive_database_is_tightened(self):
        path = self.root / "existing.sqlite3"
        path.write_bytes(b"")
        path.chmod(0o644)
        prepare_private_sqlite_path(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is unavailable")
    def test_symbolic_link_is_rejected(self):
        target = self.root / "target.sqlite3"
        target.write_bytes(b"do not modify")
        link = self.root / "linked.sqlite3"
        link.symlink_to(target)
        with self.assertRaises(SQLitePathSecurityError):
            prepare_private_sqlite_path(link)
        self.assertEqual(target.read_bytes(), b"do not modify")

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link semantics only")
    def test_hard_link_is_rejected(self):
        target = self.root / "target.sqlite3"
        target.write_bytes(b"do not modify")
        alias = self.root / "alias.sqlite3"
        os.link(target, alias)
        with self.assertRaises(SQLitePathSecurityError):
            prepare_private_sqlite_path(alias)

    def test_corrupt_database_fails_integrity_check_without_modification(self):
        path = self.root / "corrupt.sqlite3"
        original = b"not a sqlite database"
        path.write_bytes(original)
        with self.assertRaises(SQLiteIntegrityError):
            verify_sqlite_integrity(path)
        self.assertEqual(path.read_bytes(), original)

    def test_integrity_check_fails_closed_when_path_inspection_is_denied(self):
        path = self.root / "unavailable.sqlite3"
        with patch("core.sqlite_file_security.Path.lstat", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(SQLiteIntegrityError, "cannot be inspected safely"):
                verify_sqlite_integrity(path)

    def test_integrity_check_rejects_symbolic_link_without_opening_target(self):
        target = self.root / "target.sqlite3"
        target.write_bytes(b"not a database")
        link = self.root / "linked.sqlite3"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(SQLiteIntegrityError, "not a private regular file"):
            verify_sqlite_integrity(link)
        self.assertEqual(target.read_bytes(), b"not a database")

    def test_preexisting_empty_database_is_not_reinitialized(self):
        path = self.root / "empty-existing.sqlite3"
        path.touch()
        with self.assertRaises(SQLiteIntegrityError):
            ConversationStore(path)
        self.assertEqual(path.stat().st_size, 0)

    def test_conversation_store_does_not_initialize_over_corrupt_database(self):
        path = self.root / "corrupt-conversations.sqlite3"
        original = b"corrupt user data must be preserved"
        path.write_bytes(original)
        with self.assertRaises(SQLiteIntegrityError):
            ConversationStore(path)
        self.assertEqual(path.read_bytes(), original)

    def test_approval_stores_do_not_initialize_over_corrupt_databases(self):
        constructors = (
            ("corrupt-audit.sqlite3", SQLiteApprovalAuditLog),
            ("corrupt-pending.sqlite3", SQLitePendingApprovalStore),
            ("corrupt-workflow.sqlite3", WorkflowRunStore),
        )
        for filename, construct in constructors:
            with self.subTest(filename=filename):
                path = self.root / filename
                original = b"preserve corrupt user state"
                path.write_bytes(original)
                with self.assertRaises(SQLiteIntegrityError):
                    construct(str(path))
                self.assertEqual(path.read_bytes(), original)

    def test_directory_is_rejected_as_database_file(self):
        directory = self.root / "database.sqlite3"
        directory.mkdir()
        with self.assertRaises(SQLitePathSecurityError):
            prepare_private_sqlite_path(directory)

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_workflow_and_approval_databases_are_owner_only(self):
        constructors = (
            ("workflow.sqlite3", WorkflowRunStore),
            ("approval-audit.sqlite3", SQLiteApprovalAuditLog),
            ("pending-approvals.sqlite3", SQLitePendingApprovalStore),
        )
        for filename, construct in constructors:
            with self.subTest(filename=filename):
                path = self.root / filename
                construct(str(path))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_conversation_wal_and_shared_memory_files_are_owner_only(self):
        path = self.root / "conversations.sqlite3"
        store = ConversationStore(path)
        with store._connect() as db:
            db.execute(
                "INSERT INTO conversations "
                "(conversation_id, owner_id, title, created_at, updated_at, version, archived) "
                "VALUES ('conversation-00000001', 'owner', 'Private', 'now', 'now', 0, 0)"
            )
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists():
                    self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
