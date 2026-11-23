import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.project_awareness import ProjectAwarenessStore


class ProjectAwarenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "awareness.sqlite3")
        self.allowed = {("alice", "project-a"), ("bob", "project-b")}
        self.verified = set()
        self.store = ProjectAwarenessStore(
            self.path,
            project_authorizer=lambda owner, project: (owner, project) in self.allowed,
            task_outcome_verifier=lambda owner, project, task, version, ref:
                (owner, task, version, ref) in self.verified,
            task_membership_authorizer=lambda owner, project, task: task == "task-1",
        )

    def append(self, kind="goal_set", **kwargs):
        return self.store.append(
            owner_id="alice", project_id="project-a",
            event_kind=kind, summary="Bounded project note", **kwargs,
        )

    def test_persists_and_retrieves_owner_project_scoped_history(self):
        self.append()
        self.store.append(owner_id="bob", project_id="project-b",
                          event_kind="risk_recorded", summary="Other owner")
        records = self.store.list_recent(owner_id="alice", project_id="project-a")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event_kind, "goal_set")
        with self.assertRaises(PermissionError):
            self.store.list_recent(owner_id="alice", project_id="project-b")

    def test_verified_task_outcome_requires_trusted_human_review_reference(self):
        with self.assertRaises(PermissionError):
            self.append("task_outcome_verified", task_id="task-1",
                        task_version=3, evidence_ref="ci:run-1")
        self.verified.add(("alice", "task-1", 3, "review:approved"))
        record = self.append("task_outcome_verified", task_id="task-1",
                             task_version=3, evidence_ref="review:approved")
        self.assertEqual(record.task_version, 3)

    def test_task_version_and_reference_are_required_together(self):
        with self.assertRaises(ValueError):
            self.append("task_outcome_verified", task_id="task-1",
                        task_version=2)
        with self.assertRaises(ValueError):
            self.append("task_outcome_verified", task_id="task-1",
                        task_version=0, evidence_ref="review:approved")

    def test_event_kind_and_arbitrary_evidence_are_rejected(self):
        with self.assertRaises(ValueError):
            self.append("execute_command")
        with self.assertRaises(ValueError):
            self.append("risk_recorded", evidence_ref="unverified")
        with self.assertRaises(ValueError):
            self.append("goal_set", summary="x" * 1201)

    def test_planning_decisions_have_bounded_selected_task_ids(self):
        record = self.append("planning_decision", selected_task_ids=("a", "b"))
        self.assertEqual(record.selected_task_ids, ("a", "b"))
        with self.assertRaises(ValueError):
            self.append("risk_recorded", selected_task_ids=("task",))
        def endless():
            while True:
                yield "task"
        with self.assertRaises(ValueError):
            self.append("planning_decision", selected_task_ids=endless())

    def test_referenced_task_must_belong_to_project(self):
        with self.assertRaises(PermissionError):
            self.append("planning_decision", selected_task_ids=("not-a-member",))
        with self.assertRaises(PermissionError):
            self.append("risk_recorded", task_id="not-a-member", task_version=1)

    def test_history_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            self.store.list_recent(owner_id="alice", project_id="project-a", limit=101)

    def test_project_authorization_precedes_outcome_verification(self):
        calls = []
        store = ProjectAwarenessStore(
            self.path,
            project_authorizer=lambda owner, project: False,
            task_outcome_verifier=lambda *args: calls.append(args) or True,
            task_membership_authorizer=lambda *args: True,
        )
        with self.assertRaises(PermissionError):
            store.append(owner_id="mallory", project_id="private",
                         event_kind="task_outcome_verified", summary="Attempt",
                         task_id="task", task_version=1, evidence_ref="review:ref")
        self.assertEqual(calls, [])

    def test_records_are_append_only(self):
        record = self.append()
        with sqlite3.connect(self.path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE project_awareness_events SET summary='changed' WHERE event_id=?",
                           (record.event_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM project_awareness_events WHERE event_id=?",
                           (record.event_id,))


if __name__ == "__main__":
    unittest.main()
