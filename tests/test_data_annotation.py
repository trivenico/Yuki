import tempfile
import unittest
from pathlib import Path

from core.data_annotation import (
    AnnotationConflict,
    SQLiteAnnotationWorkbench,
)


class DataAnnotationWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "annotation.sqlite3"
        self.store = SQLiteAnnotationWorkbench(str(self.database))
        self.project = self.store.create_project(
            "owner:alice",
            name="Wildlife image labels",
            task_type="image_classification",
            instructions="Choose the primary animal visible in each image.",
            rubric={"labels": ["bird", "cat", "dog"], "allow_unknown": True},
        )

    def tearDown(self):
        self.directory.cleanup()

    def _task(self):
        return self.store.create_task(
            "owner:alice", self.project.project_id,
            external_ref="batch-001/item-0001",
            asset_ref="asset:images:0001",
            input_data={"prompt": "Classify the primary animal."},
        )

    def test_owner_scoped_project_and_task_access(self):
        task = self._task()
        self.assertIsNone(self.store.get_project("owner:bob", self.project.project_id))
        self.assertIsNone(self.store.get_task("owner:bob", task.task_id))
        self.assertEqual(self.store.list_projects("owner:bob"), [])
        self.assertEqual(self.store.list_tasks("owner:bob", self.project.project_id), [])

    def test_task_claim_requires_optimistic_version(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        self.assertEqual(claimed.status, "in_progress")
        self.assertEqual(claimed.version, task.version + 1)
        with self.assertRaises(AnnotationConflict):
            self.store.claim_task(
                "owner:alice", task.task_id, expected_version=task.version
            )

    def test_ai_assistance_is_prohibited_by_default(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        with self.assertRaisesRegex(PermissionError, "ai_assistance_not_permitted"):
            self.store.submit(
                "owner:alice", task.task_id, payload={"label": "bird"},
                expected_version=claimed.version, ai_assisted=True,
                human_confirmed=True,
            )

    def test_ai_assisted_work_requires_explicit_human_confirmation(self):
        project = self.store.create_project(
            "owner:alice", name="Permitted AI review",
            task_type="text_classification", instructions="Review text.",
            rubric={"labels": ["positive", "negative"]},
            ai_assistance_mode="permitted_with_disclosure",
        )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="ai-1",
            input_data={"text": "Good"},
        )
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        with self.assertRaisesRegex(PermissionError, "human_confirmation_required"):
            self.store.submit(
                "owner:alice", task.task_id,
                payload={"label": "positive"}, expected_version=claimed.version,
                ai_assisted=True, human_confirmed=False,
            )
        submission = self.store.submit(
            "owner:alice", task.task_id,
            payload={"label": "positive"}, expected_version=claimed.version,
            ai_assisted=True, human_confirmed=True,
        )
        self.assertTrue(submission.ai_assisted)
        self.assertTrue(submission.human_confirmed)
        self.assertEqual(submission.status, "submitted")

    def test_review_updates_submission_and_task_state(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        submission = self.store.submit(
            "owner:alice", task.task_id,
            payload={"label": "bird"}, expected_version=claimed.version,
            human_confirmed=True,
        )
        reviewed = self.store.review(
            "owner:alice", submission.submission_id,
            decision="rework", notes="Use the project taxonomy exactly.",
        )
        self.assertEqual(reviewed.status, "rework")
        updated_task = self.store.get_task("owner:alice", task.task_id)
        self.assertEqual(updated_task.status, "rework")
        self.assertGreater(updated_task.version, claimed.version)

    def test_project_completion_requires_all_tasks_accepted_and_is_terminal(self):
        task = self._task()
        with self.assertRaisesRegex(ValueError, "has_unreviewed_tasks"):
            self.store.set_project_status(
                "owner:alice", self.project.project_id, status="completed"
            )
        self.store.set_project_status(
            "owner:alice", self.project.project_id, status="paused"
        )
        with self.assertRaisesRegex(ValueError, "has_unreviewed_tasks"):
            self.store.set_project_status(
                "owner:alice", self.project.project_id, status="completed"
            )
        self.store.set_project_status(
            "owner:alice", self.project.project_id, status="active"
        )
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        submission = self.store.submit(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_version=claimed.version, human_confirmed=True,
        )
        self.store.review(
            "owner:alice", submission.submission_id, decision="accepted"
        )
        self.store.set_project_status(
            "owner:alice", self.project.project_id, status="completed"
        )
        with self.assertRaisesRegex(ValueError, "invalid_annotation_project_transition"):
            self.store.set_project_status(
                "owner:alice", self.project.project_id, status="active"
            )

    def test_project_pause_prevents_new_tasks_and_submissions(self):
        self.store.set_project_status(
            "owner:alice", self.project.project_id, status="paused"
        )
        with self.assertRaisesRegex(ValueError, "project_not_active"):
            self._task()

    def test_rejects_remote_asset_urls_and_oversized_payloads(self):
        with self.assertRaisesRegex(ValueError, "opaque_storage_identifier"):
            self.store.create_task(
                "owner:alice", self.project.project_id,
                external_ref="remote", asset_ref="https://example.com/image.jpg",
                input_data={},
            )
        with self.assertRaisesRegex(ValueError, "too_large"):
            self.store.create_task(
                "owner:alice", self.project.project_id,
                external_ref="large", input_data={"text": "x" * 70000},
            )

    def test_rejects_non_json_and_non_finite_payloads(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        with self.assertRaisesRegex(ValueError, "invalid_annotation_payload"):
            self.store.submit(
                "owner:alice", task.task_id, payload={"score": float("nan")},
                expected_version=claimed.version, human_confirmed=True,
            )

    def test_invalid_typed_submission_is_rejected_without_advancing_task(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        with self.assertRaisesRegex(ValueError, "label_not_in_project_rubric"):
            self.store.submit(
                "owner:alice", task.task_id, payload={"label": "airplane"},
                expected_version=claimed.version, human_confirmed=True,
            )
        unchanged = self.store.get_task("owner:alice", task.task_id)
        self.assertEqual(unchanged.status, "in_progress")
        self.assertEqual(unchanged.version, claimed.version)
        self.assertEqual(self.store.list_submissions("owner:alice", task.task_id), [])

    def test_audit_trail_does_not_store_annotation_content(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        self.store.submit(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_version=claimed.version, ai_assisted=False, human_confirmed=True,
        )
        events = self.store.audit_events("owner:alice")
        serialized = repr(events)
        self.assertNotIn("bird", serialized)
        self.assertIn("submission_created", serialized)

    def test_batch_import_is_atomic_and_preserves_order(self):
        tasks = [
            {"external_ref": "batch-a", "input_data": {"text": "one"}},
            {"external_ref": "batch-b", "asset_ref": "asset:two",
             "input_data": {"text": "two"}},
        ]
        created = self.store.create_tasks(
            "owner:alice", self.project.project_id, tasks=tasks
        )
        self.assertEqual([item.external_ref for item in created], ["batch-a", "batch-b"])
        with self.assertRaisesRegex(ValueError, "duplicate_external_task_reference_in_batch"):
            self.store.create_tasks(
                "owner:alice", self.project.project_id,
                tasks=[
                    {"external_ref": "batch-c", "input_data": {}},
                    {"external_ref": "batch-c", "input_data": {}},
                ],
            )
        self.assertEqual(len(self.store.list_tasks(
            "owner:alice", self.project.project_id
        )), 2)

    def test_accepted_export_excludes_unreviewed_and_other_owners(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        submission = self.store.submit(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_version=claimed.version, ai_assisted=False, human_confirmed=True,
        )
        self.assertEqual(self.store.export_accepted(
            "owner:alice", self.project.project_id
        ), [])
        self.store.review(
            "owner:alice", submission.submission_id, decision="accepted"
        )
        exported = self.store.export_accepted(
            "owner:alice", self.project.project_id
        )
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["annotation"], {"label": "bird"})
        self.assertFalse(exported[0]["ai_assisted"])
        self.assertEqual(
            self.store.export_accepted("owner:bob", self.project.project_id), []
        )

    def test_duplicate_external_task_reference_is_rejected(self):
        self._task()
        with self.assertRaisesRegex(ValueError, "duplicate_external_task_reference"):
            self._task()
        with self.assertRaisesRegex(ValueError, "duplicate_external_task_reference"):
            self.store.create_tasks(
                "owner:alice", self.project.project_id,
                tasks=[{"external_ref": "batch-001/item-0001", "input_data": {}}],
            )

    def test_legacy_project_schema_migrates_to_human_only_ai_policy(self):
        import sqlite3
        legacy_path = Path(self.directory.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy_path)
        db.execute("""CREATE TABLE annotation_projects (
            project_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL,
            task_type TEXT NOT NULL, instructions TEXT NOT NULL, rubric_json TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        db.execute(
            "INSERT INTO annotation_projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a" * 32, "owner:legacy", "Legacy", "custom", "Review",
             "{}", "active", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        db.commit()
        db.close()
        migrated = SQLiteAnnotationWorkbench(str(legacy_path))
        project = migrated.get_project("owner:legacy", "a" * 32)
        self.assertEqual(project.ai_assistance_mode, "human_only")

    def test_drafts_are_owner_scoped_durable_and_revision_checked(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        first = self.store.save_draft(
            "owner:alice", task.task_id, payload={"boxes": []}, expected_revision=0
        )
        self.assertEqual(first.revision, 1)
        self.assertIsNone(self.store.get_draft("owner:bob", task.task_id))
        self.assertEqual(
            self.store.get_draft("owner:alice", task.task_id).payload, {"boxes": []}
        )
        second = self.store.save_draft(
            "owner:alice", task.task_id, payload={"boxes": [{"x": 0.1}]},
            expected_revision=first.revision,
        )
        self.assertEqual(second.revision, 2)
        with self.assertRaises(AnnotationConflict):
            self.store.save_draft(
                "owner:alice", task.task_id, payload={"boxes": []},
                expected_revision=first.revision,
            )
        with self.assertRaises(AnnotationConflict):
            self.store.delete_draft(
                "owner:alice", task.task_id, expected_revision=first.revision
            )
        self.assertEqual(
            self.store.get_task("owner:alice", task.task_id).version, claimed.version
        )
        self.assertTrue(self.store.delete_draft(
            "owner:alice", task.task_id, expected_revision=second.revision
        ))
        self.assertIsNone(self.store.get_draft("owner:alice", task.task_id))

    def test_draft_rejects_oversized_payload_and_noneditable_task(self):
        task = self._task()
        with self.assertRaisesRegex(ValueError, "not_editable"):
            self.store.save_draft(
                "owner:alice", task.task_id, payload={"label": "bird"},
                expected_revision=0,
            )
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        with self.assertRaisesRegex(ValueError, "too_large"):
            self.store.save_draft(
                "owner:alice", task.task_id, payload={"text": "x" * (70 * 1024)},
                expected_revision=0,
            )
        self.assertEqual(
            self.store.get_task("owner:alice", task.task_id).version, claimed.version
        )

    def test_successful_submission_atomically_clears_draft(self):
        task = self._task()
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        self.store.save_draft(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_revision=0,
        )
        self.store.submit(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_version=claimed.version, human_confirmed=True,
        )
        self.assertIsNone(self.store.get_draft("owner:alice", task.task_id))

    def test_database_symlink_is_rejected(self):
        if not hasattr(Path, "symlink_to"):
            self.skipTest("symlinks are unavailable")
        target = Path(self.directory.name) / "target.sqlite3"
        target.touch()
        link = Path(self.directory.name) / "link.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not permitted")
        with self.assertRaisesRegex(ValueError, "must_not_be_symlink"):
            SQLiteAnnotationWorkbench(str(link))


if __name__ == "__main__":
    unittest.main()
