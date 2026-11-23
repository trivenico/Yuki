import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.project_registry import ProjectRegistry
from ui.project_views import render_project, render_projects


class ProjectDashboardViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = ProjectRegistry(
            str(Path(self.tmp.name) / "projects.sqlite3"),
            task_owner_resolver=lambda owner, task: owner if task == "task-1" else None,
        )
        self.project = self.registry.create_project(owner_id="alice", name="Yuki MVP")

    def test_project_list_is_owner_scoped(self):
        rendered = render_projects(self.registry, actor_id="alice")
        self.assertIn(self.project.project_id, rendered)
        self.assertNotIn(self.project.project_id, render_projects(self.registry, actor_id="bob"))

    def test_project_detail_uses_authorized_task_metadata_and_milestones(self):
        self.registry.add_task(owner_id="alice", project_id=self.project.project_id, task_id="task-1")
        milestone = self.registry.create_milestone(
            owner_id="alice", project_id=self.project.project_id, title="API integration",
        )

        class Tasks:
            def get(self, *, actor_id, task_id):
                self.assert_actor = actor_id
                return SimpleNamespace(
                    task_id=task_id, state="succeeded", updated_at="2026-11-23T00:00:00+00:00",
                    dependencies=(), owner_id=actor_id,
                )

        rendered = render_project(
            self.registry, Tasks(), actor_id="alice", project_id=self.project.project_id,
        )
        self.assertIn("succeeded but unverified: 1", rendered)
        self.assertIn(milestone.milestone_id, rendered)
        self.assertIn("measured progress", rendered.lower())
        self.assertNotIn("result", rendered.lower())

    def test_project_detail_reports_unreadable_task_without_exposing_details(self):
        self.registry.add_task(owner_id="alice", project_id=self.project.project_id, task_id="task-1")

        class Denied:
            def get(self, **kwargs):
                raise PermissionError("private task information")

        rendered = render_project(
            self.registry, Denied(), actor_id="alice", project_id=self.project.project_id,
        )
        self.assertIn("task-1", rendered)
        self.assertIn("not readable", rendered)
        self.assertNotIn("private task information", rendered)


if __name__ == "__main__":
    unittest.main()
