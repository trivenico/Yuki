"""Tests for the annotation Textual command boundary."""
import json
import unittest
from types import SimpleNamespace

from ui.annotation_workbench import execute_annotation_command


class FakeWorkbench:
    def __init__(self):
        self.claim_args = None
        self.submit_args = None
        self.project = SimpleNamespace(
            name="Wildlife", task_type="image_classification", status="active",
            ai_assistance_mode="human_only", instructions="Choose the primary animal.",
            rubric={"labels": ["bird", "cat"]}, project_id="a" * 32,
        )
        self.task = SimpleNamespace(
            task_id="b" * 32, project_id="a" * 32, external_ref="img-1",
            status="pending", version=1, asset_ref=None, input_data={"caption": "A bird"},
        )

    def list_projects(self, owner_id, limit):
        return [self.project]

    def get_project(self, owner_id, project_id):
        return self.project if project_id == self.project.project_id else None

    def list_tasks(self, owner_id, project_id, status=None, limit=100):
        return [self.task]

    def get_task(self, owner_id, task_id):
        return self.task if task_id == self.task.task_id else None

    def claim_task(self, owner_id, task_id, *, expected_version):
        self.claim_args = (owner_id, task_id, expected_version)
        return SimpleNamespace(task_id=task_id, status="in_progress", version=2)

    def submit(self, owner_id, task_id, **kwargs):
        self.submit_args = (owner_id, task_id, kwargs)
        return SimpleNamespace(submission_id="c" * 32, status="submitted")

    def list_submissions(self, owner_id, task_id):
        return []

    def review(self, owner_id, submission_id, **kwargs):
        return SimpleNamespace(submission_id=submission_id, status=kwargs["decision"])


class AnnotationWorkbenchCommandTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeWorkbench()

    def test_projects_are_owner_scoped(self):
        result = execute_annotation_command(self.store, "owner:alice", "/projects")
        self.assertIn("Wildlife", result)

    def test_project_open_and_task_listing(self):
        self.assertIn("Choose the primary animal.", execute_annotation_command(
            self.store, "owner:alice", "/open " + "a" * 32,
        ))
        self.assertIn("img-1", execute_annotation_command(
            self.store, "owner:alice", "/tasks",
        ))

    def test_claim_uses_explicit_expected_version(self):
        result = execute_annotation_command(
            self.store, "owner:alice", "/claim " + "b" * 32 + " 1",
        )
        self.assertIn("version=2", result)
        self.assertEqual(self.store.claim_args, ("owner:alice", "b" * 32, 1))

    def test_submit_requires_literal_confirmation_and_marks_human_only(self):
        task_id = "b" * 32
        denied = execute_annotation_command(
            self.store, "owner:alice", f'/submit {task_id} 2 NO {json.dumps({"label":"bird"})}',
        )
        self.assertIn("literal CONFIRM", denied)
        result = execute_annotation_command(
            self.store, "owner:alice",
            f'/submit {task_id} 2 CONFIRM {json.dumps({"label":"bird"})}',
        )
        self.assertIn("not been delivered", result)
        self.assertFalse(self.store.submit_args[2]["ai_assisted"])
        self.assertTrue(self.store.submit_args[2]["human_confirmed"])

    def test_payload_must_be_object_and_bounded(self):
        result = execute_annotation_command(
            self.store, "owner:alice", "/submit " + "b" * 32 + ' 2 CONFIRM ["bird"]',
        )
        self.assertIn("JSON object", result)

    def test_review_is_restricted_to_supported_decisions(self):
        result = execute_annotation_command(
            self.store, "owner:alice", "/review " + "c" * 32 + " reject",
        )
        self.assertIn("accepted or rework", result)

    def test_invalid_owner_and_unknown_command_fail_closed(self):
        self.assertIn("owner identity", execute_annotation_command(
            self.store, "", "/projects",
        ))
        self.assertIn("Unknown command", execute_annotation_command(
            self.store, "owner:alice", "/shell whoami",
        ))


if __name__ == "__main__":
    unittest.main()
