import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import hashlib

from core.task_workspace import TaskWorkspaceResolver, WorkspaceSecurityError


class TaskWorkspaceResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspaces"
        self.root.mkdir(mode=0o700)
        self.resolver = TaskWorkspaceResolver(self.root)

    def test_allocates_distinct_private_workspace_per_owner_and_task(self):
        alice_task = SimpleNamespace(owner_id="alice", task_id="task-1")
        bob_task = SimpleNamespace(owner_id="bob", task_id="task-1")
        second_task = SimpleNamespace(owner_id="alice", task_id="task-2")
        alice_path = Path(self.resolver(alice_task))
        bob_path = Path(self.resolver(bob_task))
        second_path = Path(self.resolver(second_task))
        self.assertNotEqual(alice_path, bob_path)
        self.assertNotEqual(alice_path, second_path)
        for path in (alice_path, bob_path, second_path):
            self.assertTrue(path.is_dir())
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_resolves_durable_workflow_run_id(self):
        workflow_run = SimpleNamespace(owner_id="alice", run_id="run-123")
        path = Path(self.resolver(workflow_run))
        expected = Path(self.resolver(SimpleNamespace(owner_id="alice", task_id="run-123")))
        self.assertEqual(path, expected)
        self.assertTrue(path.is_dir())

    def test_rejects_legacy_unowned_task(self):
        with self.assertRaises(WorkspaceSecurityError):
            self.resolver(SimpleNamespace(task_id="legacy"))

    def test_rejects_symlinked_owner_directory(self):
        owner = "alice"
        component = hashlib.sha256(owner.encode("utf-8")).hexdigest()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir(mode=0o700)
        (self.root / component).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(WorkspaceSecurityError):
            self.resolver(SimpleNamespace(owner_id=owner, task_id="task-1"))

    def test_rejects_symlinked_workspace_root(self):
        alias = Path(self.temp.name) / "workspace-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(WorkspaceSecurityError):
            TaskWorkspaceResolver(alias)


if __name__ == "__main__":
    unittest.main()
