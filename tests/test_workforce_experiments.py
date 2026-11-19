import tempfile
import unittest
from pathlib import Path

from core.durable_task_engine import DurableTaskEngine, TaskConflict, TaskNotFound


class WorkforceExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.engine = DurableTaskEngine(str(Path(self.tempdir.name) / "tasks.sqlite"))
        self.engine.create_improvement_proposal("proposal-1", {
            "recommendation": "Review queue prioritization",
            "baseline": {"scope": "bounded_snapshot"},
        })
        self.engine.transition_improvement_proposal(
            "proposal-1", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1", decision_note="Approved for experiment design",
        )
        self.design = {
            "hypothesis": "A revised queue policy may reduce median wait time.",
            "intervention": "Document and evaluate a proposed queue-priority policy.",
            "primary_metric": "median_queue_wait_seconds",
            "preferred_direction": "decrease",
            "design_type": "pre_post_descriptive",
            "eligible_population": "New engineering tasks created during the observation window.",
            "guardrails": ["Do not change task permissions", "Do not delay urgent tasks"],
            "evaluation_window_days": 14,
            "minimum_sample_size": 20,
        }

    def tearDown(self):
        self.engine.close()
        self.tempdir.cleanup()

    def test_design_requires_approved_proposal_and_is_idempotent(self):
        first = self.engine.create_workforce_experiment("exp-1", "proposal-1", self.design)
        replay = self.engine.create_workforce_experiment("exp-1", "proposal-1", self.design)
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["state"], "proposed")
        self.assertEqual(first["design"], self.design)
        self.assertEqual(len(self.engine.list_workforce_experiments()), 1)
        changed = dict(self.design, intervention="Different intervention")
        with self.assertRaises(TaskConflict):
            self.engine.create_workforce_experiment("exp-1", "proposal-1", changed)

    def test_proposal_must_be_approved(self):
        self.engine.create_improvement_proposal("proposal-pending", {"recommendation": "Review"})
        with self.assertRaises(TaskConflict):
            self.engine.create_workforce_experiment("exp-pending", "proposal-pending", self.design)
        with self.assertRaises(TaskNotFound):
            self.engine.create_workforce_experiment("exp-missing", "missing", self.design)

    def test_design_validation_rejects_unbounded_or_malformed_values(self):
        for changes in (
            {"minimum_sample_size": True},
            {"evaluation_window_days": 0},
            {"preferred_direction": "whatever"},
            {"design_type": "uncontrolled_claim"},
            {"guardrails": "not-a-list"},
            {"hypothesis": ""},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.engine.create_workforce_experiment(
                        "bad-" + str(len(changes)) + str(list(changes)),
                        "proposal-1", dict(self.design, **changes),
                    )

    def test_human_review_transition_is_attributed_and_optimistic(self):
        self.engine.create_workforce_experiment("exp-review", "proposal-1", self.design)
        approved = self.engine.transition_workforce_experiment(
            "exp-review", expected_state="proposed", new_state="approved",
            actor_id="operator-7", decision_note="Design reviewed",
        )
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(approved["decided_by"], "operator-7")
        self.assertEqual(approved["events"][-1]["actor_id"], "operator-7")
        self.assertEqual(approved["events"][-1]["event_type"], "experiment.approved")
        self.assertEqual(approved["design"], self.design)
        with self.assertRaises(TaskConflict):
            self.engine.transition_workforce_experiment(
                "exp-review", expected_state="proposed", new_state="rejected",
                actor_id="operator-8",
            )
        with self.assertRaises(ValueError):
            self.engine.transition_workforce_experiment(
                "exp-review", expected_state="approved", new_state="running",
                actor_id="operator-8",
            )

    def test_approval_does_not_execute_or_start_experiment(self):
        self.engine.create_workforce_experiment("exp-no-run", "proposal-1", self.design)
        result = self.engine.transition_workforce_experiment(
            "exp-no-run", expected_state="proposed", new_state="approved",
            actor_id="reviewer-1",
        )
        self.assertEqual(result["state"], "approved")
        self.assertNotIn("execution", result)
        self.assertNotIn("results", result)


if __name__ == "__main__":
    unittest.main()
