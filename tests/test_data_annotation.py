import sqlite3
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

    def test_independent_assignments_are_private_and_report_classification_agreement(self):
        project = self.store.create_project(
            "owner:alice", name="Text agreement", task_type="text_classification",
            instructions="Classify the sentiment.", rubric={"labels": ["positive", "negative"]},
        )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="agreement-001",
            input_data={"text": "A positive sentence."},
        )
        assignments = self.store.create_independent_assignments(
            "owner:alice", project.project_id, task.task_id,
            ["annotator:one", "annotator:two"],
        )
        self.assertEqual(len(assignments), 2)
        self.assertEqual(self.store.list_my_independent_assignments("annotator:one")[0]["assignment_id"],
                         assignments[0]["assignment_id"])
        self.assertEqual(self.store.list_my_independent_assignments("annotator:other"), [])
        for assignment, label in zip(assignments, ("positive", "positive")):
            claimed = self.store.claim_independent_assignment(
                "annotator:" + ("one" if assignment is assignments[0] else "two"),
                assignment["assignment_id"], expected_version=1,
            )
            annotator = "annotator:one" if assignment is assignments[0] else "annotator:two"
            submitted = self.store.submit_independent_assignment(
                annotator, assignment["assignment_id"],
                payload={"label": label}, expected_version=claimed["version"],
                human_confirmed=True,
            )
            self.assertEqual(submitted["status"], "submitted")
            self.assertEqual(submitted["quality"]["status"], "pass")
        report = self.store.independent_agreement_report("owner:alice", project.project_id)
        self.assertEqual(report["evaluated_task_count"], 1)
        self.assertEqual(report["mean_pairwise_agreement"], 1.0)
        self.assertEqual(report["fleiss_kappa"], None)
        self.assertIsNone(self.store.independent_agreement_report("owner:bob", project.project_id))

    def test_independent_adjudication_requires_calibrated_reviewer_and_gates_export(self):
        project = self.store.create_project(
            "owner:alice", name="Adjudication", task_type="text_classification",
            instructions="Classify sentiment.", rubric={"labels": ["positive", "negative"]},
        )
        for index in range(5):
            gold_task = self.store.create_task(
                "owner:alice", project.project_id, external_ref=f"calibration-{index}",
                input_data={"text": f"Known calibration text {index}."},
            )
            self.store.create_gold_standard(
                "owner:alice", project.project_id, gold_task.task_id, "positive"
            )
            gold_assignments = self.store.create_independent_assignments(
                "owner:alice", project.project_id, gold_task.task_id,
                ["reviewer:calibrated", f"gold-peer:{index}"],
            )
            for assignment, annotator in zip(
                gold_assignments, ("reviewer:calibrated", f"gold-peer:{index}")
            ):
                claimed = self.store.claim_independent_assignment(
                    annotator, assignment["assignment_id"], expected_version=1
                )
                self.store.submit_independent_assignment(
                    annotator, assignment["assignment_id"], payload={"label": "positive"},
                    expected_version=claimed["version"], human_confirmed=True,
                )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="disputed-001",
            input_data={"text": "A disputed sentence."},
        )
        assignments = self.store.create_independent_assignments(
            "owner:alice", project.project_id, task.task_id,
            ["annotator:first", "annotator:second"],
        )
        for assignment, annotator, label in zip(
            assignments, ("annotator:first", "annotator:second"), ("positive", "negative")
        ):
            claimed = self.store.claim_independent_assignment(
                annotator, assignment["assignment_id"], expected_version=1
            )
            self.store.submit_independent_assignment(
                annotator, assignment["assignment_id"], payload={"label": label},
                expected_version=claimed["version"], human_confirmed=True,
            )
        self.store.grant_reviewer("owner:alice", project.project_id, "reviewer:calibrated")
        self_review_task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="self-review-001",
            input_data={"text": "A sentence for self-review."},
        )
        self_review_assignments = self.store.create_independent_assignments(
            "owner:alice", project.project_id, self_review_task.task_id,
            ["reviewer:calibrated", "annotator:peer"],
        )
        for assignment, annotator in zip(
            self_review_assignments, ("reviewer:calibrated", "annotator:peer")
        ):
            claimed = self.store.claim_independent_assignment(
                annotator, assignment["assignment_id"], expected_version=1
            )
            self.store.submit_independent_assignment(
                annotator, assignment["assignment_id"], payload={"label": "positive"},
                expected_version=claimed["version"], human_confirmed=True,
            )
        with self.assertRaisesRegex(PermissionError, "self_review_prohibited"):
            self.store.adjudicate_independent_task(
                "owner:alice", project.project_id, self_review_task.task_id,
                "reviewer:calibrated", decision="accepted", final_label="positive",
            )
        with self.assertRaisesRegex(PermissionError, "reviewer_calibration_required"):
            self.store.adjudicate_independent_task(
                "owner:alice", project.project_id, task.task_id, "reviewer:new",
                decision="accepted", final_label="positive", notes="Reviewed.",
                require_reviewer_grant=False,
            )
        with self.assertRaisesRegex(ValueError, "disagreement_requires_adjudication_notes"):
            self.store.adjudicate_independent_task(
                "owner:alice", project.project_id, task.task_id, "reviewer:calibrated",
                decision="accepted", final_label="positive",
            )
        result = self.store.adjudicate_independent_task(
            "owner:alice", project.project_id, task.task_id, "reviewer:calibrated",
            decision="accepted", final_label="positive", notes="Resolved after evidence review.",
        )
        self.assertEqual(result["submission_count"], 2)
        self.assertEqual(result["agreement"], 0.5)
        self.assertEqual(len(result["source_assignment_ids"]), 2)
        report = self.store.delivery_report("owner:alice", project.project_id)
        self.assertEqual(report["accepted_independent_task_count"], 1)
        self.assertEqual(report["accepted_delivery_count"], 1)
        exported = self.store.export_accepted("owner:alice", project.project_id)
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["annotation"], {"label": "positive"})
        self.assertEqual(exported[0]["quality"]["source"], "independent_adjudication")
        self.assertEqual(len(exported[0]["quality"]["source_assignment_ids"]), 2)
        self.assertEqual(self.store.export_accepted("owner:bob", project.project_id), [])
        with self.assertRaisesRegex(ValueError, "task_already_adjudicated"):
            self.store.adjudicate_independent_task(
                "owner:alice", project.project_id, task.task_id, "owner:alice",
                decision="accepted", final_label="positive",
            )

    def test_gold_standard_is_blinded_and_calibrates_annotators(self):
        project = self.store.create_project(
            "owner:alice", name="Gold calibration", task_type="text_classification",
            instructions="Classify the sentiment.",
            rubric={"labels": ["positive", "negative"]},
        )
        for index in range(5):
            task = self.store.create_task(
                "owner:alice", project.project_id, external_ref=f"gold-{index}",
                input_data={"text": f"Calibration sentence {index}."},
            )
            self.store.create_gold_standard(
                "owner:alice", project.project_id, task.task_id, "positive"
            )
            assignments = self.store.create_independent_assignments(
                "owner:alice", project.project_id, task.task_id,
                ["annotator:calibrated", "annotator:review"],
            )
            self.assertNotIn("gold", assignments[0])
            for assignment, annotator, label in zip(
                assignments, ("annotator:calibrated", "annotator:review"),
                ("positive", "negative"),
            ):
                inbox = self.store.list_my_independent_assignments(annotator)
                item = next(row for row in inbox if row["assignment_id"] == assignment["assignment_id"])
                self.assertNotIn("expected_label", item)
                claimed = self.store.claim_independent_assignment(
                    annotator, assignment["assignment_id"], expected_version=1
                )
                self.store.submit_independent_assignment(
                    annotator, assignment["assignment_id"], payload={"label": label},
                    expected_version=claimed["version"], human_confirmed=True,
                )
        report = self.store.gold_calibration_report("owner:alice", project.project_id)
        self.assertEqual(report["gold_standard_task_count"], 5)
        calibrated = {row["annotator_id"]: row for row in report["annotators"]}
        self.assertEqual(calibrated["annotator:calibrated"]["accuracy"], 1.0)
        self.assertEqual(calibrated["annotator:calibrated"]["status"], "calibrated")
        self.assertEqual(calibrated["annotator:review"]["accuracy"], 0.0)
        self.assertEqual(calibrated["annotator:review"]["status"], "needs_review")
        self.assertIsNone(self.store.gold_calibration_report("owner:bob", project.project_id))

    def test_gold_standard_must_be_created_before_assignment_and_match_rubric(self):
        project = self.store.create_project(
            "owner:alice", name="Gold setup", task_type="text_classification",
            instructions="Classify.", rubric={"labels": ["positive", "negative"]},
        )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="gold-setup",
            input_data={"text": "A sentence."},
        )
        with self.assertRaisesRegex(ValueError, "label_not_in_project_rubric"):
            self.store.create_gold_standard(
                "owner:alice", project.project_id, task.task_id, "unknown"
            )
        self.store.create_independent_assignments(
            "owner:alice", project.project_id, task.task_id,
            ["annotator:one", "annotator:two"],
        )
        with self.assertRaisesRegex(ValueError, "gold_standard_must_precede_annotation"):
            self.store.create_gold_standard(
                "owner:alice", project.project_id, task.task_id, "positive"
            )

    def test_independent_assignment_rejects_duplicate_and_self_assignment(self):
        project = self.store.create_project(
            "owner:alice", name="Text agreement duplicate", task_type="text_classification",
            instructions="Classify.", rubric={"labels": ["positive", "negative"]},
        )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="duplicate-001",
            input_data={"text": "Sentence."},
        )
        self.store.create_independent_assignments(
            "owner:alice", project.project_id, task.task_id,
            ["annotator:one", "annotator:two"],
        )
        with self.assertRaisesRegex(ValueError, "annotator_already_assigned"):
            self.store.create_independent_assignments(
                "owner:alice", project.project_id, task.task_id,
                ["annotator:one", "annotator:three"],
            )
        with self.assertRaisesRegex(ValueError, "invalid_independent_annotators"):
            self.store.create_independent_assignments(
                "owner:alice", project.project_id, task.task_id,
                ["owner:alice", "annotator:four"],
            )

    def test_owner_can_revoke_unsubmitted_independent_assignment(self):
        project = self.store.create_project(
            "owner:alice", name="Revocable assignments", task_type="text_classification",
            instructions="Classify.", rubric={"labels": ["positive", "negative"]},
        )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="revoke-001",
            input_data={"text": "Sentence."},
        )
        assignments = self.store.create_independent_assignments(
            "owner:alice", project.project_id, task.task_id,
            ["annotator:one", "annotator:two"],
        )
        assignment_id = assignments[0]["assignment_id"]
        self.assertTrue(self.store.revoke_independent_assignment(
            "owner:alice", project.project_id, assignment_id
        ))
        replacement = self.store.create_independent_assignments(
            "owner:alice", project.project_id, task.task_id, ["annotator:one", "annotator:three"]
        )
        self.assertNotEqual(replacement[0]["assignment_id"], assignment_id)
        self.assertIsNone(self.store.claim_independent_assignment(
            "annotator:one", assignment_id, expected_version=2
        ))
        self.assertFalse(self.store.revoke_independent_assignment(
            "owner:bob", project.project_id, assignment_id
        ))

    def test_fleiss_kappa_is_reported_for_multiple_tasks_with_constant_rater_count(self):
        project = self.store.create_project(
            "owner:alice", name="Text agreement kappa", task_type="text_classification",
            instructions="Classify.", rubric={"labels": ["bird", "cat", "dog"]},
        )
        first = self.store.create_task(
            "owner:alice", project.project_id, external_ref="kappa-001",
            input_data={"text": "First."},
        )
        second = self.store.create_task(
            "owner:alice", project.project_id, external_ref="kappa-002",
            input_data={"prompt": "Classify another animal."},
        )
        values = [(first, ("bird", "bird")), (second, ("cat", "dog"))]
        for task, labels in values:
            assignments = self.store.create_independent_assignments(
                "owner:alice", project.project_id, task.task_id,
                ["annotator:one", "annotator:two"],
            )
            for assignment, annotator, label in zip(
                assignments, ("annotator:one", "annotator:two"), labels
            ):
                claimed = self.store.claim_independent_assignment(
                    annotator, assignment["assignment_id"], expected_version=1
                )
                self.store.submit_independent_assignment(
                    annotator, assignment["assignment_id"],
                    payload={"label": label}, expected_version=claimed["version"],
                    human_confirmed=True,
                )
        report = self.store.independent_agreement_report("owner:alice", project.project_id)
        self.assertEqual(report["evaluated_task_count"], 2)
        self.assertEqual(report["mean_pairwise_agreement"], 0.5)
        self.assertAlmostEqual(report["fleiss_kappa"], 0.2)

    def test_quality_assessment_is_persisted_and_project_report_is_aggregated(self):
        project = self.store.create_project(
            "owner:alice", name="Quality labels", task_type="text_classification",
            instructions="Classify the text.",
            rubric={"labels": ["positive", "negative"], "quality": {
                "minimum_confidence": 0.8, "require_rationale": True,
            }},
        )
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="quality-001",
            input_data={"text": "A positive sentence."},
        )
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        submission = self.store.submit(
            "owner:alice", task.task_id,
            payload={"label": "positive", "confidence": 0.4},
            expected_version=claimed.version, human_confirmed=True,
        )
        self.assertEqual(submission.quality["status"], "warning")
        self.assertEqual(
            {item["code"] for item in submission.quality["findings"]},
            {"confidence_below_threshold", "rationale_missing"},
        )
        report = self.store.quality_report("owner:alice", project.project_id)
        self.assertEqual(report["submission_count"], 1)
        self.assertEqual(report["quality"], {"pass": 0, "warning": 1, "unknown": 0})
        self.assertEqual(report["review"], {"submitted": 1, "accepted": 0, "rework": 0})
        self.assertEqual(report["finding_counts"], {
            "confidence_below_threshold": 1, "rationale_missing": 1,
        })
        self.assertIsNone(self.store.quality_report("owner:bob", project.project_id))

    def test_invalid_quality_policy_is_rejected_at_project_creation(self):
        with self.assertRaisesRegex(ValueError, "invalid_quality_item_range"):
            self.store.create_project(
                "owner:alice", name="Bad policy", task_type="text_classification",
                instructions="Classify.", rubric={"quality": {
                    "minimum_items": 3, "maximum_items": 1,
                }},
            )

    def test_reviewer_grants_are_owner_scoped_persistent_and_revocable(self):
        project = self.project
        self.assertTrue(self.store.grant_reviewer(
            "owner:alice", project.project_id, "owner:reviewer"
        ))
        reopened = SQLiteAnnotationWorkbench(str(self.database))
        self.assertEqual(
            reopened.reviewer_owner("owner:reviewer", project.project_id), "owner:alice"
        )
        self.assertEqual(
            reopened.list_reviewers("owner:alice", project.project_id)[0]["reviewer_id"],
            "owner:reviewer",
        )
        with self.assertRaisesRegex(ValueError, "project_owner_cannot_be_reviewer"):
            reopened.grant_reviewer("owner:alice", project.project_id, "owner:alice")
        self.assertTrue(reopened.revoke_reviewer(
            "owner:alice", project.project_id, "owner:reviewer"
        ))
        self.assertIsNone(reopened.reviewer_owner("owner:reviewer", project.project_id))
        self.assertEqual(reopened.list_reviewers("owner:alice", project.project_id), [])

    def test_review_queue_lists_only_submitted_work_and_records_reviewer_identity(self):
        project = self.project
        task = self.store.create_task(
            "owner:alice", project.project_id, external_ref="review-queue",
            input_data={"text": "A sample"},
        )
        claimed = self.store.claim_task(
            "owner:alice", task.task_id, expected_version=task.version
        )
        submission = self.store.submit(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_version=claimed.version, human_confirmed=True,
        )
        self.store.grant_reviewer("owner:alice", project.project_id, "owner:reviewer")
        queue = self.store.list_review_queue(
            "owner:alice", project.project_id, reviewer_id="owner:reviewer"
        )
        self.assertEqual([item.submission.submission_id for item in queue], [submission.submission_id])
        with self.assertRaisesRegex(PermissionError, "self_review_prohibited"):
            self.store.review(
                "owner:alice", submission.submission_id,
                decision="accepted", reviewer_id="owner:alice",
            )
        reviewed = self.store.review(
            "owner:alice", submission.submission_id,
            decision="accepted", reviewer_id="owner:reviewer",
            expected_project_id=project.project_id,
        )
        self.assertEqual(reviewed.reviewer_id, "owner:reviewer")
        self.assertEqual(
            self.store.list_review_queue(
                "owner:alice", project.project_id, reviewer_id="owner:reviewer"
            ), []
        )

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
        self.store.save_draft(
            "owner:alice", task.task_id, payload={"private_text": "bird"},
            expected_revision=0,
        )
        self.store.submit(
            "owner:alice", task.task_id, payload={"label": "bird"},
            expected_version=claimed.version, ai_assisted=False, human_confirmed=True,
        )
        events = self.store.audit_events("owner:alice")
        serialized = repr(events)
        self.assertNotIn("bird", serialized)
        self.assertIn("submission_created", serialized)
        self.assertIn("draft_saved", serialized)

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
        self.assertEqual(exported[0]["quality"]["status"], "pass")
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
        reopened = SQLiteAnnotationWorkbench(str(self.database))
        self.assertEqual(
            reopened.get_draft("owner:alice", task.task_id).revision, 1
        )
        second = reopened.save_draft(
            "owner:alice", task.task_id, payload={"boxes": [{"x": 0.1}]},
            expected_revision=first.revision,
        )
        self.assertEqual(second.revision, 2)
        self.assertIsNone(reopened.save_draft(
            "owner:bob", task.task_id, payload={"boxes": []}, expected_revision=0
        ))
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

    def test_legacy_submission_schema_migrates_reviewer_identity_column(self):
        legacy_path = Path(self.directory.name) / "legacy-annotation.sqlite3"
        db = sqlite3.connect(legacy_path)
        try:
            db.execute("""CREATE TABLE annotation_submissions (
                submission_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                project_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                payload_json TEXT NOT NULL, ai_assisted INTEGER NOT NULL,
                human_confirmed INTEGER NOT NULL, status TEXT NOT NULL,
                reviewer_notes TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            db.commit()
        finally:
            db.close()
        SQLiteAnnotationWorkbench(str(legacy_path))
        db = sqlite3.connect(legacy_path)
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(annotation_submissions)")}
        finally:
            db.close()
        self.assertIn("reviewer_id", columns)
        self.assertIn("quality_json", columns)

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


class AnnotationReworkAndManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "annotation.sqlite3"
        self.store = SQLiteAnnotationWorkbench(str(self.database))
        self.project = self.store.create_project(
            "owner:alice", name="Rework lifecycle", task_type="text_classification",
            instructions="Classify sentiment.", rubric={"labels": ["positive", "negative"]},
        )
        self.task = self.store.create_task(
            "owner:alice", self.project.project_id, external_ref="rework-001",
            input_data={"text": "A sentence requiring review."},
        )

    def tearDown(self):
        self.directory.cleanup()

    def _submit_round(self, assignments, labels):
        for assignment, label in zip(assignments, labels):
            annotator = assignment["annotator_id"]
            claimed = self.store.claim_independent_assignment(
                annotator, assignment["assignment_id"], expected_version=1,
            )
            self.store.submit_independent_assignment(
                annotator, assignment["assignment_id"], payload={"label": label},
                expected_version=claimed["version"], human_confirmed=True,
            )

    def test_rework_round_preserves_history_and_manifest_is_reproducible(self):
        first = self.store.create_independent_assignments(
            "owner:alice", self.project.project_id, self.task.task_id,
            ["annotator:first-a", "annotator:first-b"],
        )
        first_with_workers = [
            dict(item, annotator_id=worker)
            for item, worker in zip(first, ("annotator:first-a", "annotator:first-b"))
        ]
        self._submit_round(first_with_workers, ["positive", "negative"])
        first_decision = self.store.adjudicate_independent_task(
            "owner:alice", self.project.project_id, self.task.task_id, "owner:alice",
            decision="rework", notes="Resolve inconsistent first-round labels.",
        )
        self.assertEqual(first_decision["round_number"], 1)
        second = self.store.create_rework_assignments(
            "owner:alice", self.project.project_id, self.task.task_id,
            ["annotator:second-a", "annotator:second-b"],
        )
        self.assertEqual({item["round_number"] for item in second}, {2})
        report = self.store.delivery_report("owner:alice", self.project.project_id)
        self.assertEqual(report["pending_adjudication_count"], 1)
        self.assertEqual(report["rework_independent_task_count"], 0)
        second_with_workers = [
            dict(item, annotator_id=worker)
            for item, worker in zip(second, ("annotator:second-a", "annotator:second-b"))
        ]
        self._submit_round(second_with_workers, ["positive", "positive"])
        accepted = self.store.adjudicate_independent_task(
            "owner:alice", self.project.project_id, self.task.task_id, "owner:alice",
            decision="accepted", final_label="positive",
        )
        self.assertEqual(accepted["round_number"], 2)
        with sqlite3.connect(self.database) as db:
            events = db.execute(
                "SELECT round_number, event_type FROM annotation_adjudication_events "
                "WHERE owner_id=? AND project_id=? AND task_id=? ORDER BY round_number",
                ("owner:alice", self.project.project_id, self.task.task_id),
            ).fetchall()
            assignments = db.execute(
                "SELECT assignment_id, round_number, status FROM annotation_independent_assignments "
                "WHERE task_id=? ORDER BY round_number, assignment_id", (self.task.task_id,),
            ).fetchall()
        self.assertEqual([row[0] for row in events], [1, 2])
        self.assertEqual(len(assignments), 4)
        self.assertEqual(len({row[0] for row in assignments}), 4)
        manifest = self.store.create_delivery_manifest(
            "owner:alice", self.project.project_id, idempotency_key="delivery-001",
        )
        self.assertEqual(manifest["item_count"], 1)
        full_manifest = self.store.get_delivery_manifest(
            "owner:alice", self.project.project_id, manifest["manifest_id"],
        )
        self.assertEqual(full_manifest["snapshot"]["items"][0]["annotation"], {"label": "positive"})
        repeated = self.store.create_delivery_manifest(
            "owner:alice", self.project.project_id, idempotency_key="delivery-001",
        )
        self.assertEqual(repeated["manifest_id"], manifest["manifest_id"])
        verification = self.store.verify_delivery_manifest(
            "owner:alice", self.project.project_id, manifest["manifest_id"],
        )
        self.assertTrue(verification["valid"])
        self.assertIsNone(self.store.verify_delivery_manifest(
            "owner:bob", self.project.project_id, manifest["manifest_id"],
        ))
        with sqlite3.connect(self.database) as db:
            db.execute("DROP TRIGGER annotation_delivery_manifests_no_update")
            db.execute(
                "UPDATE annotation_delivery_manifests SET snapshot_json=? WHERE manifest_id=?",
                ('{"tampered":true}', manifest["manifest_id"]),
            )
        tampered = self.store.verify_delivery_manifest(
            "owner:alice", self.project.project_id, manifest["manifest_id"],
        )
        self.assertFalse(tampered["valid"])

    def test_rework_requires_new_distinct_annotators_and_current_round(self):
        first = self.store.create_independent_assignments(
            "owner:alice", self.project.project_id, self.task.task_id,
            ["annotator:one", "annotator:two"],
        )
        self._submit_round([
            dict(first[0], annotator_id="annotator:one"),
            dict(first[1], annotator_id="annotator:two"),
        ], ["positive", "negative"])
        self.store.adjudicate_independent_task(
            "owner:alice", self.project.project_id, self.task.task_id, "owner:alice",
            decision="rework", notes="Second pass required.",
        )
        with self.assertRaisesRegex(ValueError, "rework_requires_new_annotators"):
            self.store.create_rework_assignments(
                "owner:alice", self.project.project_id, self.task.task_id,
                ["annotator:one", "annotator:three"],
            )


if __name__ == "__main__":
    unittest.main()
