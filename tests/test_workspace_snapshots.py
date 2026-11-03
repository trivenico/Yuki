import hashlib
import tempfile
import unittest
from pathlib import Path

from automation.workspace_snapshots import SnapshotError, WorkspaceSnapshotStore


class WorkspaceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.storage = Path(self.tmp.name) / "private" / "snapshots"
        self.file = self.root / "app.py"
        self.file.write_text("VALUE = 1\n", encoding="utf-8")
        self.store = WorkspaceSnapshotStore(
            self.root, self.storage,
            authorize=lambda actor, action, resource: actor == "alice",
        )

    def test_snapshot_and_compare_and_swap_restore(self):
        snap = self.store.create(actor_id="alice", paths=["app.py"])
        self.file.write_text("VALUE = 2\n", encoding="utf-8")
        expected = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.store.restore(actor_id="alice", snapshot_id=snap.snapshot_id,
                           expected_current={"app.py": expected})
        self.assertEqual(self.file.read_text(encoding="utf-8"), "VALUE = 1\n")

    def test_restore_rejects_concurrent_modification(self):
        snap = self.store.create(actor_id="alice", paths=["app.py"])
        self.file.write_text("VALUE = 3\n", encoding="utf-8")
        with self.assertRaises(SnapshotError):
            self.store.restore(actor_id="alice", snapshot_id=snap.snapshot_id,
                               expected_current={"app.py": "incorrect"})
        self.assertEqual(self.file.read_text(encoding="utf-8"), "VALUE = 3\n")

    def test_rejects_traversal_and_secret_files(self):
        (self.root / ".env").write_text("SECRET=x", encoding="utf-8")
        for path in ("../outside", ".env"):
            with self.assertRaises(SnapshotError):
                self.store.create(actor_id="alice", paths=[path])

    def test_authorization_fails_closed(self):
        with self.assertRaises(PermissionError):
            self.store.create(actor_id="mallory", paths=["app.py"])

    def test_blob_integrity_is_checked_before_restore(self):
        snap = self.store.create(actor_id="alice", paths=["app.py"])
        blob = self.storage / "blobs" / snap.files[0].sha256
        blob.write_bytes(b"tampered")
        self.file.write_text("VALUE = 2\n", encoding="utf-8")
        expected = hashlib.sha256(self.file.read_bytes()).hexdigest()
        with self.assertRaises(SnapshotError):
            self.store.restore(actor_id="alice", snapshot_id=snap.snapshot_id,
                               expected_current={"app.py": expected})
        self.assertEqual(self.file.read_text(encoding="utf-8"), "VALUE = 2\n")


if __name__ == "__main__":
    unittest.main()
