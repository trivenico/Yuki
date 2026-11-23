import tempfile
import unittest
from pathlib import Path

from core.project_registry import ProjectRegistry


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owners = {"task-a": "alice", "task-b": "bob"}
        self.registry = ProjectRegistry(
            str(Path(self.tmp.name) / "projects.sqlite3"),
            task_owner_resolver=lambda owner_id, task_id: self.owners.get(task_id),
        )

    def test_project_membership_is_durable_and_owner_scoped(self):
        project = self.registry.create_project(owner_id="alice", name="Yuki")
        self.registry.add_task(owner_id="alice", project_id=project.project_id, task_id="task-a")
        self.assertEqual(self.registry.list_task_ids(owner_id="alice", project_id=project.project_id), ("task-a",))
        reopened = ProjectRegistry(
            str(Path(self.tmp.name) / "projects.sqlite3"),
            task_owner_resolver=lambda task_id: self.owners.get(task_id),
        )
        self.assertEqual(reopened.list_task_ids(owner_id="alice", project_id=project.project_id), ("task-a",))
        with self.assertRaises(LookupError):
            reopened.list_task_ids(owner_id="bob", project_id=project.project_id)

    def test_cross_owner_task_membership_is_rejected(self):
        project = self.registry.create_project(owner_id="alice", name="Yuki")
        with self.assertRaises(PermissionError):
            self.registry.add_task(owner_id="alice", project_id=project.project_id, task_id="task-b")
        with self.assertRaises(PermissionError):
            self.registry.add_task(owner_id="alice", project_id=project.project_id, task_id="unknown")

    def test_membership_requires_owned_project(self):
        with self.assertRaises(LookupError):
            self.registry.add_task(owner_id="alice", project_id="missing", task_id="task-a")

    def test_milestone_completion_requires_evidence_and_version(self):
        project = self.registry.create_project(owner_id="alice", name="Yuki")
        milestone = self.registry.create_milestone(
            owner_id="alice", project_id=project.project_id, title="MVP", due_at="2026-12-01T12:00:00+04:00",
        )
        with self.assertRaises(ValueError):
            self.registry.complete_milestone(
                owner_id="alice", milestone_id=milestone.milestone_id,
                expected_version=1, verification_ref=" ",
            )
        completed = self.registry.complete_milestone(
            owner_id="alice", milestone_id=milestone.milestone_id,
            expected_version=1, verification_ref="ci:run-99",
        )
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.version, 2)
        self.assertEqual(completed.verification_ref, "ci:run-99")
        with self.assertRaises(LookupError):
            self.registry.complete_milestone(
                owner_id="alice", milestone_id=milestone.milestone_id,
                expected_version=1, verification_ref="ci:run-100",
            )

    def test_milestone_isolation_and_timezone_validation(self):
        project = self.registry.create_project(owner_id="alice", name="Yuki")
        with self.assertRaises(ValueError):
            self.registry.create_milestone(
                owner_id="alice", project_id=project.project_id, title="Bad", due_at="2026-12-01T12:00:00",
            )
        milestone = self.registry.create_milestone(owner_id="alice", project_id=project.project_id, title="MVP")
        with self.assertRaises(LookupError):
            self.registry.complete_milestone(
                owner_id="bob", milestone_id=milestone.milestone_id,
                expected_version=1, verification_ref="proof",
            )

    def test_project_names_are_unique_per_owner(self):
        self.registry.create_project(owner_id="alice", name="Yuki")
        with self.assertRaises(ValueError):
            self.registry.create_project(owner_id="alice", name="Yuki")
        self.registry.create_project(owner_id="bob", name="Yuki")


if __name__ == "__main__":
    unittest.main()
