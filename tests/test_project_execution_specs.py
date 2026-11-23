import tempfile
import unittest
from pathlib import Path

from automation.agent_execution_spec import AgentExecutionSpec
from core.execution_spec_registry import ExecutionSpecRegistry
from core.project_execution_specs import ProjectExecutionSpecBindings


class ProjectExecutionSpecBindingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.specs = ExecutionSpecRegistry(
            str(root / "specs.sqlite3"), authorize=lambda actor, owner, action: actor == owner
        )
        self.specs.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec(1))
        self.specs.put(actor_id="owner-a", owner_id="owner-a", spec=self.spec(2))
        self.member = True
        self.allowed = True
        self.bindings = ProjectExecutionSpecBindings(
            str(root / "bindings.sqlite3"), spec_registry=self.specs,
            authorize_project=lambda actor, owner, project: self.allowed and actor == owner and project == "project-a",
            task_is_member=lambda owner, project, task: self.member and owner == "owner-a" and project == "project-a" and task == "task-a",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def spec(version):
        return AgentExecutionSpec(
            spec_id="review", role="reviewer", instructions="Review the approved change.",
            capabilities=("coding",), version=version,
        )

    def test_binding_pins_exact_immutable_spec_version(self):
        self.assertEqual(self.bindings.bind(
            actor_id="owner-a", owner_id="owner-a", project_id="project-a",
            task_id="task-a", spec_id="review", spec_version=1,
        ), 1)
        self.assertEqual(self.bindings.get(
            actor_id="owner-a", owner_id="owner-a", project_id="project-a", task_id="task-a",
        ), ("review", 1))
        self.bindings.bind(
            actor_id="owner-a", owner_id="owner-a", project_id="project-a",
            task_id="task-a", spec_id="review", spec_version=2,
        )
        self.assertEqual(self.bindings.get(
            actor_id="owner-a", owner_id="owner-a", project_id="project-a", task_id="task-a",
        ), ("review", 2))

    def test_rejects_non_member_and_denied_project_access(self):
        self.member = False
        with self.assertRaises(LookupError):
            self.bindings.get(actor_id="owner-a", owner_id="owner-a", project_id="project-a", task_id="task-a")
        self.member = True
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.bindings.bind(actor_id="owner-a", owner_id="owner-a", project_id="project-a", task_id="task-a", spec_id="review", spec_version=1)

    def test_owner_isolation(self):
        with self.assertRaises(PermissionError):
            self.bindings.get(actor_id="intruder", owner_id="owner-a", project_id="project-a", task_id="task-a")

    def test_binding_requires_existing_exact_spec_version(self):
        with self.assertRaises(LookupError):
            self.bindings.bind(actor_id="owner-a", owner_id="owner-a", project_id="project-a", task_id="task-a", spec_id="review", spec_version=3)

    def test_append_only_binding_versions(self):
        self.bindings.bind(actor_id="owner-a", owner_id="owner-a", project_id="project-a", task_id="task-a", spec_id="review", spec_version=1)
        with self.assertRaises(Exception):
            with self.bindings._connect() as db:
                db.execute("UPDATE project_execution_spec_bindings SET spec_version=2")
        with self.assertRaises(Exception):
            with self.bindings._connect() as db:
                db.execute("DELETE FROM project_execution_spec_bindings")

    def test_file_backed_database_required(self):
        with self.assertRaises(ValueError):
            ProjectExecutionSpecBindings(":memory:", spec_registry=self.specs, authorize_project=lambda *_: True, task_is_member=lambda *_: True)


if __name__ == "__main__":
    unittest.main()
