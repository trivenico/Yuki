import hashlib
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from core.task_artifacts import TaskArtifactError, TaskArtifactStore
from core.task_workspace import TaskWorkspaceResolver, WorkspaceSecurityError


class TaskArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspaces"
        self.root.mkdir(mode=0o700)
        try:
            self.resolver = TaskWorkspaceResolver(self.root)
        except WorkspaceSecurityError as exc:
            self.skipTest(str(exc))
        self.grants = set()
        self.store = TaskArtifactStore(
            self.resolver,
            authorize=lambda actor, action, task_id: (actor, action, task_id) in self.grants,
        )
        self.task = SimpleNamespace(owner_id="alice", run_id="task-1")
        self.grants.update({
            ("worker", "task:artifact", "task-1"),
            ("alice", "task:artifact", "task-1"),
        })

    def test_round_trip_is_immutable_and_content_addressed(self):
        content = b"report bytes"
        reference = self.store.put(actor_id="worker", task=self.task, content=content)
        self.assertEqual(reference, "yuki-artifact://sha256/" + hashlib.sha256(content).hexdigest())
        self.assertEqual(self.store.put(actor_id="worker", task=self.task, content=content), reference)
        self.assertEqual(self.store.get(actor_id="alice", task=self.task, reference=reference), content)

    def test_concurrent_duplicate_writes_publish_one_complete_artifact(self):
        content = b"concurrent immutable output" * 1024
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs = list(pool.map(
                lambda _: self.store.put(actor_id="worker", task=self.task, content=content),
                range(4),
            ))
        self.assertEqual(len(set(refs)), 1)
        self.assertEqual(self.store.get(actor_id="alice", task=self.task, reference=refs[0]), content)

    def test_authorization_is_checked_for_each_operation(self):
        reference = self.store.put(actor_id="worker", task=self.task, content=b"private")
        with self.assertRaises(PermissionError):
            self.store.get(actor_id="intruder", task=self.task, reference=reference)

    def test_reference_cannot_resolve_in_another_tasks_workspace(self):
        reference = self.store.put(actor_id="worker", task=self.task, content=b"task scoped")
        other = SimpleNamespace(owner_id="bob", task_id="task-2")
        self.grants.add(("bob", "task:artifact", "task-2"))
        with self.assertRaises(TaskArtifactError):
            self.store.get(actor_id="bob", task=other, reference=reference)

    def test_rejects_path_like_or_external_references(self):
        with self.assertRaises(ValueError):
            self.store.get(actor_id="alice", task=self.task, reference="../../etc/passwd")
        with self.assertRaises(ValueError):
            self.store.get(actor_id="alice", task=self.task, reference="file:///tmp/report")

    def test_detects_artifact_content_tampering(self):
        reference = self.store.put(actor_id="worker", task=self.task, content=b"original")
        digest = reference.rsplit("/", 1)[-1]
        artifact_dir = Path(self.resolver(self.task)) / "artifacts"
        path = artifact_dir / digest
        path.chmod(0o600)
        path.write_bytes(b"tampered")
        with self.assertRaises(TaskArtifactError):
            self.store.get(actor_id="alice", task=self.task, reference=reference)

    def test_rejects_symlinked_artifact_directory(self):
        workspace = Path(self.resolver(self.task))
        outside = Path(self.temp.name) / "outside"
        outside.mkdir(mode=0o700)
        (workspace / "artifacts").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(TaskArtifactError):
            self.store.put(actor_id="worker", task=self.task, content=b"blocked")

    def test_enforces_artifact_size_limit(self):
        small = TaskArtifactStore(self.resolver, authorize=lambda *_: True, max_artifact_bytes=4)
        with self.assertRaises(ValueError):
            small.put(actor_id="worker", task=self.task, content=b"12345")


if __name__ == "__main__":
    unittest.main()
