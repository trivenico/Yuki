import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from core.durable_task_engine import DurableTaskEngine, TaskConflict, TaskNotFound


class DurableTaskEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "tasks.sqlite"
        self.engine = DurableTaskEngine(self.db)
        self.goal = self.engine.create_goal()

    def tearDown(self):
        self.tmp.cleanup()

    def test_randomized_experiment_assignment_is_deterministic_and_exclusive(self):
        proposal = {"recommendation": "Evaluate queue triage"}
        self.engine.create_improvement_proposal("assignment-proposal", proposal)
        self.engine.transition_improvement_proposal(
            "assignment-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed triage checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": ["No permission changes"],
            "evaluation_window_days": 14,
            "minimum_sample_size": 10,
        }
        self.engine.create_workforce_experiment("assignment-exp", "assignment-proposal", design)
        with self.assertRaises(TaskConflict):
            self.engine.assign_workforce_experiment_task("assignment-exp", "missing-task")
        self.engine.transition_workforce_experiment(
            "assignment-exp", expected_state="proposed", new_state="approved", actor_id="reviewer-2",
        )
        task = self.engine.submit(self.goal, "workforce.task", {}, idempotency_key="assignment-task")
        first = self.engine.assign_workforce_experiment_task("assignment-exp", task["id"])
        replay = self.engine.assign_workforce_experiment_task("assignment-exp", task["id"])
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["assignment"], replay["assignment"])
        self.assertIn(first["assignment"], {"control", "treatment"})
        self.engine.create_improvement_proposal("second-assignment-proposal", {"recommendation": "Second review"})
        self.engine.transition_improvement_proposal(
            "second-assignment-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        self.engine.create_workforce_experiment("second-assignment-exp", "second-assignment-proposal", design)
        self.engine.transition_workforce_experiment(
            "second-assignment-exp", expected_state="proposed", new_state="approved", actor_id="reviewer-3",
        )
        with self.assertRaises(TaskConflict):
            self.engine.assign_workforce_experiment_task("second-assignment-exp", task["id"])
        assignments = self.engine.list_workforce_experiment_assignments("assignment-exp")
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["task_id"], task["id"])
        self.assertEqual(self.engine.get_workforce_experiment("assignment-exp")["events"][-1]["event_type"],
                         "experiment.task_assigned")

    def test_experiment_outcomes_are_derived_from_task_lifecycle(self):
        self.engine.create_improvement_proposal("outcome-proposal", {"recommendation": "Measure outcomes"})
        self.engine.transition_improvement_proposal(
            "outcome-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed triage checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 14,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment("outcome-exp", "outcome-proposal", design)
        self.engine.transition_workforce_experiment(
            "outcome-exp", expected_state="proposed", new_state="approved",
            actor_id="reviewer-2",
        )
        assigned = []
        for index in range(40):
            task = self.engine.submit(
                self.goal, "workforce.task", {}, idempotency_key=f"outcome-{index}",
                max_attempts=1,
            )
            arm = self.engine.assign_workforce_experiment_task("outcome-exp", task["id"])["assignment"]
            assigned.append((task, arm))
        selected = {}
        for task, arm in assigned:
            selected.setdefault(arm, []).append(task)
        self.assertEqual(set(selected), {"control", "treatment"})
        pending_by_arm = {"control": 0, "treatment": 0}
        for arm, tasks in selected.items():
            self.engine.cancel_task(tasks[0]["id"])
            delayed = self.engine.submit(
                self.goal, "workforce.task", {}, idempotency_key=f"pending-{arm}",
                delay_seconds=86400,
            )
            delayed_arm = self.engine.assign_workforce_experiment_task("outcome-exp", delayed["id"])["assignment"]
            pending_by_arm[delayed_arm] += 1
        outcome_index = {"control": 0, "treatment": 0}
        while True:
            claimed = self.engine.claim("outcome-worker")
            if claimed is None:
                break
            arm = next(arm for task, arm in assigned if task["id"] == claimed["id"])
            if outcome_index[arm] % 2 == 0:
                self.engine.complete(claimed["id"], "outcome-worker", {"ok": True})
            else:
                self.engine.fail(claimed["id"], "outcome-worker", "redacted")
            outcome_index[arm] += 1
        report = self.engine.get_workforce_experiment_outcomes("outcome-exp")
        self.assertEqual(report["total_assigned"], 42)
        self.assertEqual(report["total_evaluable"], 38)
        self.assertEqual(report["status"], "descriptive_outcomes_available")
        for arm in ("control", "treatment"):
            self.assertEqual(report["arms"][arm]["cancelled"], 1)
            self.assertEqual(report["arms"][arm]["pending"], pending_by_arm[arm])
            self.assertEqual(report["arms"][arm]["assigned"], len(selected[arm]) + pending_by_arm[arm])
            self.assertEqual(report["arms"][arm]["evaluable"], len(selected[arm]) - 1)
            self.assertGreater(report["arms"][arm]["success_rate"], 0)
            self.assertLess(report["arms"][arm]["success_rate"], 1)
        self.assertFalse(report["automatic_action"])
        self.assertIn("no causal effect", report["interpretation"])

    def test_experiment_worker_execution_telemetry_requires_live_lease_and_is_reported(self):
        self.engine.create_improvement_proposal("execution-telemetry-proposal", {"recommendation": "Measure worker runs"})
        self.engine.transition_improvement_proposal(
            "execution-telemetry-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment(
            "execution-telemetry-exp", "execution-telemetry-proposal", design,
        )
        self.engine.transition_workforce_experiment(
            "execution-telemetry-exp", expected_state="proposed", new_state="approved",
            actor_id="reviewer-2",
        )
        task = self.engine.submit(
            self.goal, "workforce.task", {}, idempotency_key="execution-telemetry-task",
        )
        self.engine.assign_workforce_experiment_task("execution-telemetry-exp", task["id"])
        with self.assertRaises(TaskConflict):
            self.engine.record_task_experiment_execution_event(
                task["id"], "execution_started", worker_id="worker-1", attempt=1,
            )
        claimed = self.engine.claim("worker-1", task_type="workforce.task")
        self.assertEqual(claimed["id"], task["id"])
        started = self.engine.record_task_experiment_execution_event(
            task["id"], "execution_started", worker_id="worker-1", attempt=1,
        )
        replay = self.engine.record_task_experiment_execution_event(
            task["id"], "execution_started", worker_id="worker-1", attempt=1,
        )
        self.assertTrue(started["created"])
        self.assertFalse(replay["created"])
        terminal = self.engine.record_task_experiment_execution_event(
            task["id"], "execution_succeeded", worker_id="worker-1", attempt=1,
        )
        self.assertTrue(terminal["created"])
        with self.assertRaises(TaskConflict):
            self.engine.record_task_experiment_execution_event(
                task["id"], "execution_failed", worker_id="worker-1", attempt=1,
            )
        events = self.engine.list_workforce_experiment_execution_events("execution-telemetry-exp")
        self.assertEqual([item["event_type"] for item in events],
                         ["execution_started", "execution_succeeded"])
        report = self.engine.get_workforce_experiment_integrity("execution-telemetry-exp")
        self.assertEqual(report["worker_execution_events"]["execution_started"], 1)
        self.assertEqual(report["worker_execution_events"]["execution_succeeded"], 1)
        self.assertIn("not that a specific treatment was delivered", " ".join(report["limitations"]))

    def test_treatment_exposure_requires_treatment_arm_and_live_worker_lease(self):
        self.engine.create_improvement_proposal("treatment-exposure-proposal", {"recommendation": "Verify actual treatment invocation"})
        self.engine.transition_improvement_proposal(
            "treatment-exposure-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Use the reviewed triage checklist as reference context",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment(
            "treatment-exposure-exp", "treatment-exposure-proposal", design,
        )
        self.engine.transition_workforce_experiment(
            "treatment-exposure-exp", expected_state="proposed", new_state="approved",
            actor_id="reviewer-2",
        )
        treatment_task = None
        control_task = None
        for index in range(20):
            task = self.engine.submit(
                self.goal, "workforce.task", {}, idempotency_key=f"treatment-exposure-{index}",
            )
            assignment = self.engine.assign_workforce_experiment_task(
                "treatment-exposure-exp", task["id"],
            )
            if assignment["assignment"] == "treatment":
                treatment_task = task
            else:
                control_task = task
            if treatment_task and control_task:
                break
        self.assertIsNotNone(treatment_task)
        self.assertIsNotNone(control_task)
        context = self.engine.get_task_workforce_experiment_context(treatment_task["id"])
        self.assertEqual(context["assignment"], "treatment")
        with self.assertRaises(TaskConflict):
            self.engine.record_workforce_experiment_treatment_exposure(
                "treatment-exposure-exp", control_task["id"], worker_id="worker-1",
                attempt=1, intervention_version=context["intervention_version"],
                skill_name="triage",
            )
        with self.assertRaises(TaskConflict):
            self.engine.record_workforce_experiment_treatment_exposure(
                "treatment-exposure-exp", treatment_task["id"], worker_id="worker-1",
                attempt=1, intervention_version=context["intervention_version"],
                skill_name="triage",
            )
        claimed = self.engine.claim("worker-1", task_type="workforce.task")
        while claimed is not None and claimed["id"] != treatment_task["id"]:
            self.engine.reject(claimed["id"], "worker-1", "skip_non_target_test_task")
            claimed = self.engine.claim("worker-1", task_type="workforce.task")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], treatment_task["id"])
        self.engine.record_task_experiment_execution_event(
            treatment_task["id"], "execution_started", worker_id="worker-1", attempt=1,
        )
        exposure = self.engine.record_workforce_experiment_treatment_exposure(
            "treatment-exposure-exp", treatment_task["id"], worker_id="worker-1",
            attempt=1, intervention_version=context["intervention_version"],
            skill_name="triage",
        )
        replay = self.engine.record_workforce_experiment_treatment_exposure(
            "treatment-exposure-exp", treatment_task["id"], worker_id="worker-1",
            attempt=1, intervention_version=context["intervention_version"],
            skill_name="triage",
        )
        self.assertTrue(exposure["created"])
        self.assertFalse(replay["created"])
        listed = self.engine.list_workforce_experiment_treatment_exposures("treatment-exposure-exp")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["skill_name"], "triage")
        report = self.engine.get_workforce_experiment_integrity("treatment-exposure-exp")
        self.assertEqual(report["treatment_exposures"], 1)
        self.assertNotIn("treatment_execution_without_skill_exposure",
                         {issue["code"] for issue in report["issues"]})

    def test_experiment_integrity_audit_detects_missing_delivery_and_eligibility_drift(self):
        self.engine.create_improvement_proposal("integrity-proposal", {"recommendation": "Audit exposure"})
        self.engine.transition_improvement_proposal(
            "integrity-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment("integrity-exp", "integrity-proposal", design)
        self.engine.transition_workforce_experiment(
            "integrity-exp", expected_state="proposed", new_state="approved",
            actor_id="reviewer-2",
        )
        treatment_task = None
        for index in range(20):
            task = self.engine.submit(
                self.goal, "workforce.task", {}, idempotency_key=f"integrity-{index}",
            )
            assignment = self.engine.assign_workforce_experiment_task("integrity-exp", task["id"])
            if assignment["assignment"] == "treatment":
                treatment_task = task
                break
        self.assertIsNotNone(treatment_task)
        report = self.engine.get_workforce_experiment_integrity("integrity-exp")
        self.assertEqual(report["status"], "integrity_warnings")
        self.assertTrue(any(issue["code"] == "treatment_delivery_unrecorded"
                            for issue in report["issues"]))
        connection = self.engine._connect()
        try:
            connection.execute(
                "UPDATE tasks SET task_type='billing.refund' WHERE id=?",
                (treatment_task["id"],),
            )
            connection.commit()
        finally:
            connection.close()
        report = self.engine.get_workforce_experiment_integrity("integrity-exp")
        self.assertTrue(any(issue["code"] == "eligibility_drift"
                            for issue in report["issues"]))
        self.assertFalse(report["automatic_action"])
        self.assertTrue(any("not proof" in item for item in report["limitations"]))

    def test_treatment_delivery_attestations_are_append_only_and_reported(self):
        self.engine.create_improvement_proposal("delivery-proposal", {"recommendation": "Measure delivery"})
        self.engine.transition_improvement_proposal(
            "delivery-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment("delivery-exp", "delivery-proposal", design)
        self.engine.transition_workforce_experiment(
            "delivery-exp", expected_state="proposed", new_state="approved",
            actor_id="reviewer-2",
        )
        treatment_task = None
        control_task = None
        for index in range(20):
            task = self.engine.submit(
                self.goal, "workforce.task", {}, idempotency_key=f"delivery-{index}",
            )
            assignment = self.engine.assign_workforce_experiment_task("delivery-exp", task["id"])
            if assignment["assignment"] == "treatment" and treatment_task is None:
                treatment_task = task
            if assignment["assignment"] == "control" and control_task is None:
                control_task = task
            if treatment_task and control_task:
                break
        self.assertIsNotNone(treatment_task)
        self.assertIsNotNone(control_task)
        with self.assertRaises(TaskConflict):
            self.engine.record_workforce_experiment_delivery(
                "delivery-exp", control_task["id"], "delivered",
                actor_id="operator-1", intervention_version="v1",
            )
        with self.assertRaises(ValueError):
            self.engine.record_workforce_experiment_delivery(
                "delivery-exp", treatment_task["id"], "delivered",
                actor_id="operator-1",
            )
        first = self.engine.record_workforce_experiment_delivery(
            "delivery-exp", treatment_task["id"], "delivered",
            actor_id="operator-1", intervention_version="checklist-v1",
            evidence_reference="review-record-123",
        )
        replay = self.engine.record_workforce_experiment_delivery(
            "delivery-exp", treatment_task["id"], "delivered",
            actor_id="operator-1", intervention_version="checklist-v1",
            evidence_reference="review-record-123",
        )
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertFalse(first["treatment_executed"])
        with self.assertRaises(TaskConflict):
            self.engine.record_workforce_experiment_delivery(
                "delivery-exp", treatment_task["id"], "not_delivered",
                actor_id="operator-1",
            )
        listed = self.engine.list_workforce_experiment_deliveries("delivery-exp")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["actor_id"], "operator-1")
        connection = self.engine._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE workforce_experiment_deliveries SET delivery_state='unknown' "
                    "WHERE experiment_id=? AND task_id=?",
                    ("delivery-exp", treatment_task["id"]),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM workforce_experiment_deliveries WHERE experiment_id=? AND task_id=?",
                    ("delivery-exp", treatment_task["id"]),
                )
        finally:
            connection.close()
        report = self.engine.get_workforce_experiment_outcomes("delivery-exp")
        self.assertEqual(report["arms"]["treatment"]["delivery_attestations"]["delivered"], 1)
        self.assertEqual(report["arms"]["treatment"]["delivery_attestations"]["unrecorded"],
                         report["arms"]["treatment"]["assigned"] - 1)
        self.assertIn("not proof of execution", report["interpretation"])

    def test_randomized_assignment_requires_structured_matching_eligibility(self):
        self.engine.create_improvement_proposal("eligibility-proposal", {"recommendation": "Scope tasks"})
        self.engine.transition_improvement_proposal(
            "eligibility-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        free_text_design = {
            "hypothesis": "Reduce failures", "intervention": "Checklist",
            "primary_metric": "failed_tasks", "preferred_direction": "decrease",
            "design_type": "randomized_controlled", "eligible_population": "Workforce tasks",
            "guardrails": [], "evaluation_window_days": 7, "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment("free-text-eligibility", "eligibility-proposal", free_text_design)
        self.engine.transition_workforce_experiment(
            "free-text-eligibility", expected_state="proposed", new_state="approved",
            actor_id="reviewer-2",
        )
        wrong_type = self.engine.submit(
            self.goal, "billing.refund", {}, idempotency_key="wrong-eligibility",
        )
        with self.assertRaises(TaskConflict):
            self.engine.assign_workforce_experiment_task("free-text-eligibility", wrong_type["id"])
        matching = self.engine.submit(
            self.goal, "workforce.task", {}, idempotency_key="matching-eligibility",
        )
        with self.assertRaises(TaskConflict):
            self.engine.assign_workforce_experiment_task("free-text-eligibility", matching["id"])

        structured_design = dict(free_text_design)
        structured_design["eligible_population"] = {
            "description": "Only workforce tasks",
            "task_types": ["workforce.task"],
        }
        self.engine.create_improvement_proposal("structured-eligibility-proposal", {"recommendation": "Scope tasks"})
        self.engine.transition_improvement_proposal(
            "structured-eligibility-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-3",
        )
        self.engine.create_workforce_experiment(
            "structured-eligibility", "structured-eligibility-proposal", structured_design,
        )
        self.engine.transition_workforce_experiment(
            "structured-eligibility", expected_state="proposed", new_state="approved",
            actor_id="reviewer-4",
        )
        wrong_type2 = self.engine.submit(
            self.goal, "billing.refund", {}, idempotency_key="wrong-eligibility-2",
        )
        with self.assertRaises(TaskConflict):
            self.engine.assign_workforce_experiment_task("structured-eligibility", wrong_type2["id"])
        matching2 = self.engine.submit(
            self.goal, "workforce.task", {}, idempotency_key="matching-eligibility-2",
        )
        result = self.engine.assign_workforce_experiment_task("structured-eligibility", matching2["id"])
        self.assertIn(result["assignment"], {"control", "treatment"})

    def test_pre_post_experiment_cannot_assign_randomized_arms(self):
        self.engine.create_improvement_proposal("descriptive-proposal", {"recommendation": "Observe"})
        self.engine.transition_improvement_proposal(
            "descriptive-proposal", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        design = {
            "hypothesis": "Observe trend",
            "intervention": "Document existing process",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "pre_post_descriptive",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 5,
        }
        self.engine.create_workforce_experiment("descriptive-exp", "descriptive-proposal", design)
        self.engine.transition_workforce_experiment(
            "descriptive-exp", expected_state="proposed", new_state="approved", actor_id="reviewer-2",
        )
        task = self.engine.submit(self.goal, "workforce.task", {}, idempotency_key="descriptive-task")
        with self.assertRaises(TaskConflict):
            self.engine.assign_workforce_experiment_task("descriptive-exp", task["id"])

    def test_engine_close_is_idempotent_and_blocks_future_operations(self):
        engine = DurableTaskEngine(":memory:")
        goal = engine.create_goal()
        engine.close()
        engine.close()
        with self.assertRaises(RuntimeError):
            engine.get_goal(goal)

    def test_engine_context_manager_closes_resources(self):
        with DurableTaskEngine(":memory:") as engine:
            goal = engine.create_goal()
            self.assertEqual(engine.get_goal(goal)["state"], "active")
        with self.assertRaises(RuntimeError):
            engine.get_goal(goal)

    def test_in_memory_database_persists_across_engine_connections(self):
        engine = DurableTaskEngine(":memory:")
        goal = engine.create_goal()
        task = engine.submit(
            goal, "work", {"ok": True}, idempotency_key="memory-task"
        )
        claimed = engine.claim("memory-worker")
        self.assertEqual(claimed["id"], task["id"])
        completed = engine.complete(task["id"], "memory-worker", {"done": True})
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(engine.get_task(task["id"])["result"], {"done": True})

    def _create_reviewed_source_task(self, key="knowledge-source"):
        task = self.engine.submit(self.goal, "workforce.task", {}, idempotency_key=key, max_attempts=1)
        claimed = self.engine.claim("knowledge-test-worker")
        self.engine.complete(task["id"], "knowledge-test-worker", {
            "ok": True,
            "review_required": True,
            "review_evidence": {
                "reviewer_id": "independent-reviewer",
                "status": "approved",
                "approved": True,
            },
        })
        return task

    def test_knowledge_candidate_requires_independently_reviewed_success(self):
        unreviewed = self.engine.submit(self.goal, "workforce.task", {}, idempotency_key="unreviewed-source", max_attempts=1)
        self.engine.claim("knowledge-test-worker")
        self.engine.complete(unreviewed["id"], "knowledge-test-worker", {
            "ok": True, "review_required": False, "review_evidence": None,
        })
        lesson = {"title": "Prefer bounded retries", "lesson": "Use a single attempt for side-effecting tasks.", "domain": "workforce", "tags": ["reliability"]}
        with self.assertRaises(TaskConflict):
            self.engine.create_knowledge_candidate("lesson-unreviewed", unreviewed["id"], lesson)

        failed = self.engine.submit(self.goal, "workforce.task", {}, idempotency_key="failed-source", max_attempts=1)
        self.engine.claim("knowledge-test-worker")
        self.engine.fail(failed["id"], "knowledge-test-worker", "redacted failure")
        with self.assertRaises(TaskConflict):
            self.engine.create_knowledge_candidate("lesson-failed", failed["id"], lesson)

    def test_knowledge_candidate_is_durable_idempotent_and_requires_human_verification(self):
        task = self._create_reviewed_source_task()
        lesson = {
            "title": "Preserve task idempotency",
            "lesson": "Use stable idempotency keys when retrying a planned task graph.",
            "domain": "workforce",
            "tags": ["reliability", "recovery"],
        }
        first = self.engine.create_knowledge_candidate("lesson-idempotency", task["id"], lesson)
        self.assertTrue(first["created"])
        self.assertEqual(first["state"], "candidate")
        self.assertEqual(first["events"][0]["event_type"], "knowledge.proposed")
        replay = self.engine.create_knowledge_candidate("another-id", task["id"], lesson)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["knowledge_id"], "lesson-idempotency")
        self.assertEqual(self.engine.list_verified_knowledge(), [])
        verified = self.engine.transition_knowledge(
            "lesson-idempotency", expected_state="candidate", new_state="verified",
            actor_id="human-reviewer", review_note="Checked against source task and confirmed.",
        )
        self.assertEqual(verified["state"], "verified")
        self.assertEqual(verified["reviewed_by"], "human-reviewer")
        self.assertEqual(len(self.engine.list_verified_knowledge(domain="workforce")), 1)
        with self.assertRaises(TaskConflict):
            self.engine.transition_knowledge(
                "lesson-idempotency", expected_state="candidate", new_state="verified",
                actor_id="second-reviewer",
            )
        reopened = DurableTaskEngine(self.db)
        self.assertEqual(reopened.get_knowledge("lesson-idempotency")["state"], "verified")

    def test_knowledge_candidate_rejects_obvious_secrets_and_contact_identifiers(self):
        task = self._create_reviewed_source_task("knowledge-sensitive-source")
        base = {"title": "Operational lesson", "lesson": "Use bounded retries.", "domain": "workforce", "tags": []}
        for value in (
            "Contact admin@example.com for access.",
            "password=correct-horse-battery-staple",
            "Bearer abcdefghijklmnopqrstuvwxyz",
            "-----BEGIN PRIVATE KEY-----",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.engine.create_knowledge_candidate(
                        "sensitive-" + str(len(value)), task["id"],
                        {**base, "lesson": value},
                    )

    def test_knowledge_candidate_rejects_unbounded_or_extra_fields(self):
        task = self._create_reviewed_source_task("knowledge-validation")
        lesson = {"title": "Valid title", "lesson": "Valid lesson", "domain": "workforce", "tags": []}
        with self.assertRaises(ValueError):
            self.engine.create_knowledge_candidate("bad-extra", task["id"], {**lesson, "raw_chat": "private"})
        with self.assertRaises(ValueError):
            self.engine.create_knowledge_candidate("bad-title", task["id"], {**lesson, "title": " "})
        with self.assertRaises(ValueError):
            self.engine.create_knowledge_candidate("bad-tags", task["id"], {**lesson, "tags": ["duplicate", "DUPLICATE"]})

    def test_knowledge_revision_requires_human_approval_and_preserves_history(self):
        source = self._create_reviewed_source_task("knowledge-revision-source")
        original = {
            "title": "Prefer bounded retries",
            "lesson": "Use bounded retries for transient failures.",
            "domain": "workforce",
            "tags": ["reliability"],
        }
        self.engine.create_knowledge_candidate("revision-lesson", source["id"], original)
        self.engine.transition_knowledge(
            "revision-lesson", expected_state="candidate", new_state="verified",
            actor_id="initial-curator",
        )
        revised = {**original, "lesson": "Use bounded retries with jitter for transient failures."}
        proposal = self.engine.create_knowledge_revision("revision-lesson", "revision-2", revised)
        self.assertTrue(proposal["created"])
        self.assertEqual(proposal["state"], "proposed")
        self.assertEqual(self.engine.get_knowledge("revision-lesson")["knowledge"], original)
        self.assertEqual(self.engine.list_verified_knowledge()[0]["knowledge"], original)
        with self.assertRaises(TaskConflict):
            self.engine.transition_knowledge_revision(
                "revision-2", expected_state="proposed", new_state="approved",
                actor_id="",
            )
        approved = self.engine.transition_knowledge_revision(
            "revision-2", expected_state="proposed", new_state="approved",
            actor_id="independent-curator", review_note="Compared both versions and source evidence.",
        )
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(approved["reviewed_by"], "independent-curator")
        current = self.engine.get_knowledge("revision-lesson")
        self.assertEqual(current["knowledge"], revised)
        history = self.engine.list_knowledge_revisions("revision-lesson")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["version"], 1)
        self.assertEqual(history[0]["knowledge"], original)
        self.assertEqual(history[0]["state"], "approved")
        self.assertEqual(history[1]["version"], 2)
        replay = self.engine.create_knowledge_revision("revision-lesson", "revision-2", revised)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["version"], 2)

    def test_knowledge_revision_rejects_stale_or_unverified_parent(self):
        source = self._create_reviewed_source_task("knowledge-revision-stale-source")
        lesson = {
            "title": "Keep evidence",
            "lesson": "Preserve evidence references.",
            "domain": "workforce",
            "tags": [],
        }
        self.engine.create_knowledge_candidate("revision-stale-lesson", source["id"], lesson)
        with self.assertRaises(TaskConflict):
            self.engine.create_knowledge_revision("revision-stale-lesson", "revision-before-verification", lesson)
        self.engine.transition_knowledge(
            "revision-stale-lesson", expected_state="candidate", new_state="verified",
            actor_id="curator",
        )
        self.engine.create_knowledge_revision("revision-stale-lesson", "revision-reject", {**lesson, "lesson": "Alternative lesson."})
        rejected = self.engine.transition_knowledge_revision(
            "revision-reject", expected_state="proposed", new_state="rejected",
            actor_id="curator",
        )
        self.assertEqual(rejected["state"], "rejected")
        self.assertEqual(self.engine.get_knowledge("revision-stale-lesson")["knowledge"], lesson)

    def test_improvement_proposal_lifecycle_is_durable_and_actor_attributed(self):
        proposal = {
            "category": "department_backlog",
            "priority": "medium",
            "recommendation": "Review queue prioritization",
            "evidence": {"queued_tasks": 3, "window": "bounded"},
        }
        created = self.engine.create_improvement_proposal("proposal-1", proposal)
        self.assertTrue(created["created"])
        replay = self.engine.create_improvement_proposal("proposal-1", dict(reversed(list(proposal.items()))))
        self.assertFalse(replay["created"])
        self.assertEqual(replay["state"], "proposed")
        with self.assertRaises(TaskConflict):
            self.engine.create_improvement_proposal("proposal-1", {**proposal, "priority": "high"})

        approved = self.engine.transition_improvement_proposal(
            "proposal-1", expected_state="proposed", new_state="approved",
            actor_id="operator-7", decision_note="Reviewed evidence",
        )
        self.assertEqual(approved["decided_by"], "operator-7")
        self.assertEqual(approved["state"], "approved")
        with self.assertRaises(TaskConflict):
            self.engine.transition_improvement_proposal(
                "proposal-1", expected_state="proposed", new_state="rejected",
                actor_id="operator-8",
            )
        completed = self.engine.transition_improvement_proposal(
            "proposal-1", expected_state="approved", new_state="completed",
            actor_id="operator-7", outcome={"observed_change": "queue reduced", "causal_claim": False},
        )
        self.assertEqual(completed["outcome"]["causal_claim"], False)
        self.assertEqual([event["actor_id"] for event in completed["events"]], ["system", "operator-7", "operator-7"])

        restarted = DurableTaskEngine(self.db)
        persisted = restarted.get_improvement_proposal("proposal-1")
        self.assertEqual(persisted["state"], "completed")
        self.assertEqual(persisted["outcome"]["observed_change"], "queue reduced")

    def test_improvement_proposal_rejects_invalid_transitions_and_unbounded_fields(self):
        self.engine.create_improvement_proposal("proposal-2", {"recommendation": "Inspect"})
        with self.assertRaises(TaskConflict):
            self.engine.transition_improvement_proposal(
                "proposal-2", expected_state="proposed", new_state="completed",
                actor_id="operator-1", outcome={"observed_change": "x"},
            )
        with self.assertRaises(ValueError):
            self.engine.transition_improvement_proposal(
                "proposal-2", expected_state="proposed", new_state="approved",
                actor_id="operator-1", decision_note="x" * 2001,
            )
        with self.assertRaises(ValueError):
            self.engine.transition_improvement_proposal(
                "proposal-2", expected_state="proposed", new_state="rejected",
                actor_id="operator-1", outcome={"not_allowed": True},
            )
        with self.assertRaises(ValueError):
            self.engine.transition_improvement_proposal(
                "proposal-2", expected_state="proposed", new_state="approved",
                actor_id=" ",
            )
        with self.assertRaises(ValueError):
            self.engine.list_improvement_proposals(limit=True)

    def test_task_survives_engine_restart_and_emits_events(self):
        task = self.engine.submit(self.goal, "summarize", {"text": "hello"}, idempotency_key="one")
        again = DurableTaskEngine(self.db)
        self.assertEqual(again.get_task(task["id"])["payload"], {"text": "hello"})
        self.assertEqual([e["event_type"] for e in again.list_events(self.goal)], ["goal.created", "task.queued"])

    def test_idempotency_returns_same_task_but_rejects_different_payload(self):
        a = self.engine.submit(self.goal, "summarize", {"v": 1}, idempotency_key="same")
        b = self.engine.submit(self.goal, "summarize", {"v": 1}, idempotency_key="same")
        self.assertEqual(a["id"], b["id"])
        with self.assertRaises(TaskConflict):
            self.engine.submit(self.goal, "summarize", {"v": 2}, idempotency_key="same")

    def test_idempotent_replay_after_goal_completion_returns_original_task(self):
        task = self.engine.submit(
            self.goal, "work", {"value": 1}, idempotency_key="terminal-replay"
        )
        self.engine.claim("terminal-replay-worker")
        self.engine.complete(task["id"], "terminal-replay-worker", {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "completed")
        replay = self.engine.submit(
            self.goal, "work", {"value": 1}, idempotency_key="terminal-replay"
        )
        self.assertEqual(replay["id"], task["id"])
        with self.assertRaises(TaskConflict):
            self.engine.submit(
                self.goal, "work", {"value": 2}, idempotency_key="terminal-replay"
            )

    def test_idempotency_canonicalizes_payload_key_order(self):
        first = self.engine.submit(
            self.goal, "work", {"a": 1, "b": 2}, idempotency_key="canonical"
        )
        replay = self.engine.submit(
            self.goal, "work", {"b": 2, "a": 1}, idempotency_key="canonical"
        )
        self.assertEqual(first["id"], replay["id"])

    def test_idempotency_rejects_changed_execution_options(self):
        parent = self.engine.submit(self.goal, "parent", {}, idempotency_key="parent")
        self.engine.submit(self.goal, "child", {"v": 1}, idempotency_key="child", dependencies=[parent["id"]], max_attempts=2, delay_seconds=3)
        for kwargs in (
            {"dependencies": [], "max_attempts": 2, "delay_seconds": 3},
            {"dependencies": [parent["id"]], "max_attempts": 3, "delay_seconds": 3},
            {"dependencies": [parent["id"]], "max_attempts": 2, "delay_seconds": 4},
        ):
            with self.assertRaises(TaskConflict):
                self.engine.submit(self.goal, "child", {"v": 1}, idempotency_key="child", **kwargs)

    def test_dependencies_reject_strings_and_malformed_ids(self):
        with self.assertRaises(ValueError):
            self.engine.submit(self.goal, "work", {}, idempotency_key="string-deps", dependencies="task-id")
        with self.assertRaises(ValueError):
            self.engine.submit(self.goal, "work", {}, idempotency_key="bad-dep", dependencies=[None])

    def test_worker_error_details_are_not_persisted(self):
        task = self.engine.submit(self.goal, "work", {}, idempotency_key="redact", max_attempts=1)
        self.engine.claim("worker")
        secret = "api_key=very-secret-value"
        self.engine.fail(task["id"], "worker", secret)
        self.assertNotIn("very-secret-value", self.engine.get_task(task["id"])["last_error"])
        events = self.engine.list_events(self.goal)
        self.assertNotIn("very-secret-value", str(events))

    def test_invalid_lease_and_delay_numbers_are_rejected(self):
        for index, invalid_delay in enumerate((float("nan"), float("inf"), True, 10 ** 1000)):
            with self.subTest(delay_type=type(invalid_delay).__name__, index=index):
                with self.assertRaises(ValueError):
                    self.engine.submit(
                        self.goal, "work", {},
                        idempotency_key=f"invalid-delay-{index}",
                        delay_seconds=invalid_delay,
                    )
        with self.assertRaises(ValueError):
            self.engine.claim("worker", lease_seconds=True)
        with self.assertRaises(ValueError):
            self.engine.heartbeat("missing", "worker", lease_seconds=True)

        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="invalid-retry-delay"
        )
        self.engine.claim("worker")
        for invalid_retry_delay in (float("inf"), float("nan"), 10 ** 1000, True):
            with self.subTest(retry_delay_type=type(invalid_retry_delay).__name__):
                with self.assertRaises(ValueError):
                    self.engine.fail(
                        task["id"], "worker", "error",
                        retry_delay_seconds=invalid_retry_delay,
                    )

    def test_dependencies_gate_claim_until_parent_succeeds(self):
        first = self.engine.submit(self.goal, "collect", {}, idempotency_key="a")
        second = self.engine.submit(self.goal, "analyze", {}, idempotency_key="b", dependencies=[first["id"]])
        self.assertEqual(self.engine.claim("worker")["id"], first["id"])
        self.assertIsNone(self.engine.claim("worker"))
        self.engine.complete(first["id"], "worker", {"ok": True})
        self.assertEqual(self.engine.claim("worker")["id"], second["id"])

    def test_failure_retries_only_to_configured_attempt_limit(self):
        task = self.engine.submit(self.goal, "transient", {}, idempotency_key="retry", max_attempts=2)
        claimed = self.engine.claim("worker")
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(self.engine.fail(task["id"], "worker", "temporary")["state"], "queued")
        claimed = self.engine.claim("worker")
        self.assertEqual(claimed["attempts"], 2)
        self.assertEqual(self.engine.fail(task["id"], "worker", "permanent")["state"], "failed")

    def test_failed_dependency_marks_descendant_failed_instead_of_stranding_it(self):
        parent = self.engine.submit(self.goal, "parent", {}, idempotency_key="parent", max_attempts=1)
        child = self.engine.submit(self.goal, "child", {}, idempotency_key="child", dependencies=[parent["id"]])
        self.engine.claim("worker")
        self.engine.fail(parent["id"], "worker", "permanent failure")
        self.assertEqual(self.engine.get_task(child["id"])["state"], "failed")
        self.assertIsNone(self.engine.claim("worker"))

    def test_expired_lease_recovery_and_stale_worker_rejected(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        goal = engine.create_goal()
        task = engine.submit(goal, "work", {}, idempotency_key="lease", max_attempts=2)
        engine.claim("worker-a", lease_seconds=5)
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        with self.assertRaises(TaskConflict):
            engine.complete(task["id"], "worker-a", {})
        self.assertEqual(engine.claim("worker-b")["id"], task["id"])

    def test_concurrent_workers_claim_distinct_tasks(self):
        first = self.engine.submit(self.goal, "work", {"n": 1}, idempotency_key="concurrent-1")
        second = self.engine.submit(self.goal, "work", {"n": 2}, idempotency_key="concurrent-2")
        barrier = threading.Barrier(2)
        claimed = []
        errors = []

        def claim(worker):
            try:
                barrier.wait(timeout=5)
                claimed.append(self.engine.claim(worker)["id"])
            except Exception as exc:
                errors.append(exc)

        workers = [
            threading.Thread(target=claim, args=("worker-a",)),
            threading.Thread(target=claim, args=("worker-b",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(any(worker.is_alive() for worker in workers), "claim workers timed out")
        self.assertEqual(errors, [])
        self.assertEqual(len(claimed), 2)
        self.assertEqual(set(claimed), {first["id"], second["id"]})

    def test_existing_database_gets_fingerprint_migration(self):
        # Simulate the pre-fingerprint schema; opening the engine must migrate
        # additively rather than requiring a destructive database rebuild.
        legacy_db = Path(self.tmp.name) / "legacy.sqlite"
        conn = sqlite3.connect(legacy_db)
        try:
            conn.executescript("""
                CREATE TABLE goals(
                    id TEXT PRIMARY KEY, state TEXT NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE tasks(
                    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
                    task_type TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT,
                    state TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                    available_at REAL NOT NULL, lease_owner TEXT, lease_until REAL,
                    last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(goal_id,idempotency_key)
                );
            """)
        finally:
            conn.close()
        migrated = DurableTaskEngine(legacy_db)
        conn = sqlite3.connect(legacy_db)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        finally:
            conn.close()
        self.assertIn("submission_fingerprint", columns)
        goal = migrated.create_goal("migrated-goal")
        task = migrated.submit(goal, "work", {}, idempotency_key="new-task")
        self.assertIsNotNone(task["submission_fingerprint"])

        # A pre-migration row has no verifiable fingerprint and must not be
        # silently accepted as an idempotent replay with unknown options.
        conn = sqlite3.connect(legacy_db)
        try:
            conn.execute(
                """INSERT INTO tasks(
                    id, goal_id, task_type, payload_json, state, idempotency_key,
                    attempts, max_attempts, available_at, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("legacy-task", goal, "work", "{}", "queued", "legacy-key", 0, 3, 0, 0, 0),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(TaskConflict):
            migrated.submit(goal, "work", {}, idempotency_key="legacy-key")

    def test_goal_state_rejects_non_string_values_with_value_error(self):
        for invalid_state in (None, [], {}, 1):
            with self.subTest(state_type=type(invalid_state).__name__):
                with self.assertRaises(ValueError):
                    self.engine.set_goal_state(self.goal, invalid_state)

    def test_resume_reconciles_goal_completed_while_paused(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="resume-terminal-reconcile"
        )
        self.engine.claim("resume-worker")
        self.engine.set_goal_state(self.goal, "paused")
        self.engine.complete(task["id"], "resume-worker", {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "paused")
        resumed = self.engine.set_goal_state(self.goal, "active")
        self.assertEqual(resumed["state"], "completed")
        event_types = [event["event_type"] for event in self.engine.list_events(self.goal)]
        self.assertEqual(event_types.count("goal.completed"), 1)

    def test_completed_goal_cannot_be_cancelled_or_reopened(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="completed-goal-terminal"
        )
        self.engine.claim("terminal-worker")
        self.engine.complete(task["id"], "terminal-worker", {"done": True})
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "completed")
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "cancelled")
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "active")
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "completed")

    def test_goal_cancellation_emits_per_task_audit_events(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="goal-cancel-audit"
        )
        # A queued task can be cancelled atomically before any worker starts.
        self.engine.set_goal_state(self.goal, "cancelled")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "cancelled")
        events = self.engine.list_events(self.goal)
        task_cancel_events = [
            event for event in events
            if event["task_id"] == task["id"] and event["event_type"] == "task.cancelled"
        ]
        goal_cancel_events = [
            event for event in events
            if event["task_id"] is None and event["event_type"] == "goal.cancelled"
        ]
        self.assertEqual(len(task_cancel_events), 1)
        self.assertEqual(goal_cancel_events[-1]["detail"]["cancelled_task_count"], 1)

    def test_goal_cancellation_race_with_completion_has_one_terminal_task_event(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="goal-cancel-race"
        )
        self.engine.claim("race-worker")
        barrier = threading.Barrier(2)
        errors = []

        def complete():
            try:
                barrier.wait(timeout=5)
                self.engine.complete(task["id"], "race-worker", {"done": True})
            except TaskConflict:
                # Expected if goal cancellation invalidates the lease first.
                pass
            except Exception as exc:
                errors.append(exc)

        def cancel_goal():
            try:
                barrier.wait(timeout=5)
                self.engine.set_goal_state(self.goal, "cancelled")
            except TaskConflict:
                # Expected if completion commits first and makes the goal terminal.
                pass
            except Exception as exc:
                errors.append(exc)

        workers = [
            threading.Thread(target=complete),
            threading.Thread(target=cancel_goal),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(any(worker.is_alive() for worker in workers), "race workers timed out")
        self.assertEqual(errors, [])
        final_task = self.engine.get_task(task["id"])
        # The engine refuses goal cancellation while a task is running, so
        # completion must win this race without a task-cancelled event.
        self.assertEqual(final_task["state"], "succeeded")
        events = [
            event for event in self.engine.list_events(self.goal)
            if event["task_id"] == task["id"]
            and event["event_type"] in {"task.succeeded", "task.cancelled"}
        ]
        self.assertEqual(len(events), 1)

    def test_running_task_can_only_be_recovered_after_lease_expiry(self):
        now = [100.0]
        engine = DurableTaskEngine(self.db, clock=lambda: now[0])
        goal = engine.create_goal()
        task = engine.submit(
            goal, "work", {}, idempotency_key="expiry-cancel",
            max_attempts=1,
        )
        engine.claim("expiry-worker", lease_seconds=5)
        with self.assertRaises(TaskConflict):
            engine.cancel_task(task["id"])
        with self.assertRaises(TaskConflict):
            engine.set_goal_state(goal, "cancelled")
        now[0] += 6
        self.assertEqual(engine.recover_expired_leases(), 1)
        self.assertEqual(engine.get_task(task["id"])["state"], "failed")
        # Exhausting the only attempt terminally completes the goal.
        # Terminal goals cannot be reopened or cancelled afterward.
        self.assertEqual(engine.get_goal(goal)["state"], "completed")
        with self.assertRaises(TaskConflict):
            engine.set_goal_state(goal, "cancelled")

    def test_running_task_cannot_be_cancelled_while_side_effect_may_be_active(self):
        task = self.engine.submit(
            self.goal, "work", {}, idempotency_key="running-cancel-guard"
        )
        self.engine.claim("active-worker")
        with self.assertRaises(TaskConflict):
            self.engine.cancel_task(task["id"])
        with self.assertRaises(TaskConflict):
            self.engine.set_goal_state(self.goal, "cancelled")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "running")
        self.assertEqual(self.engine.get_goal(self.goal)["state"], "active")
        # The worker retains ownership and can report its real outcome.
        self.assertEqual(
            self.engine.complete(task["id"], "active-worker", {"done": True})["state"],
            "succeeded",
        )

    def test_pause_cancel_and_reject_new_work_when_goal_not_active(self):
        self.engine.set_goal_state(self.goal, "paused")
        with self.assertRaises(TaskConflict):
            self.engine.submit(self.goal, "work", {}, idempotency_key="paused")
        self.engine.set_goal_state(self.goal, "cancelled")
        with self.assertRaises(TaskConflict):
            self.engine.submit(self.goal, "work", {}, idempotency_key="cancelled")

    def test_payload_size_is_bounded(self):
        with self.assertRaises(ValueError):
            self.engine.submit(self.goal, "work", {"text": "x" * 1_000_100}, idempotency_key="large")

    def test_unknown_goal_and_task_are_clear_errors(self):
        with self.assertRaises(TaskNotFound):
            self.engine.get_task("missing")
        with self.assertRaises(TaskNotFound):
            self.engine.submit("missing", "work", {}, idempotency_key="x")


    def test_owned_goal_creation_is_idempotent_and_owner_scoped(self):
        first = self.engine.create_owned_goal(
            "user-a", "request-1", {"goal": "prepare a launch plan"}
        )
        replay = self.engine.create_owned_goal(
            "user-a", "request-1", {"goal": "prepare a launch plan"}
        )
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["goal"]["id"], replay["goal"]["id"])
        self.assertEqual(first["goal"]["owner_id"], "user-a")
        other_owner = self.engine.create_owned_goal(
            "user-b", "request-1", {"goal": "prepare a launch plan"}
        )
        self.assertNotEqual(first["goal"]["id"], other_owner["goal"]["id"])
        self.assertEqual(other_owner["goal"]["owner_id"], "user-b")

    def test_owned_goal_rejects_changed_request_for_same_owner_key(self):
        self.engine.create_owned_goal("user-a", "request-2", {"goal": "first"})
        with self.assertRaises(TaskConflict):
            self.engine.create_owned_goal("user-a", "request-2", {"goal": "changed"})

    def test_owned_goal_rejects_invalid_or_oversized_input(self):
        for owner, key in ((None, "key"), ("", "key"), ("user", ""), ("user", "x" * 257)):
            with self.subTest(owner=owner, key_length=len(key) if isinstance(key, str) else None):
                with self.assertRaises(ValueError):
                    self.engine.create_owned_goal(owner, key, {"goal": "x"})
        with self.assertRaises(ValueError):
            self.engine.create_owned_goal("user", "large", {"goal": "x" * 17_000})

    def test_legacy_goals_migrate_with_unknown_owner(self):
        legacy_db = Path(self.tmp.name) / "legacy-owner.sqlite"
        conn = sqlite3.connect(legacy_db)
        try:
            conn.execute(
                "CREATE TABLE goals(id TEXT PRIMARY KEY,state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO goals VALUES(?,?,?,?)",
                ("legacy-goal", "active", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()
        migrated = DurableTaskEngine(legacy_db)
        self.assertIsNone(migrated.get_goal("legacy-goal")["owner_id"])
        created = migrated.create_owned_goal("new-user", "new-request", {"goal": "new"})
        self.assertEqual(created["goal"]["owner_id"], "new-user")

if __name__ == "__main__":
    unittest.main()
