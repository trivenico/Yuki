import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from core.project_intelligence import (
    ProjectTaskEvidence, build_project_progress, recommend_next_project_tasks, build_project_resume_assessment,
)


NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


class ProjectIntelligenceTests(unittest.TestCase):
    def test_resume_assessment_combines_live_evidence_history_and_milestones(self):
        tasks = iter((
            ProjectTaskEvidence("done", "owner", "succeeded", NOW.isoformat(),
                                verification_ref="review:done", human_review_verified=True),
            ProjectTaskEvidence("next", "owner", "queued", NOW.isoformat(), ("done",)),
            ProjectTaskEvidence("blocked", "owner", "queued", NOW.isoformat(), ("missing",)),
            ProjectTaskEvidence("unknown-proof", "owner", "succeeded", NOW.isoformat()),
        ))
        events = (
            SimpleNamespace(owner_id="owner", project_id="yuki", event_kind="goal_set",
                            summary="Ship native runtime", created_at="2026-11-22T10:00:00+00:00", event_id="1"),
            SimpleNamespace(owner_id="owner", project_id="yuki", event_kind="goal_set",
                            summary="Finish secure resume", created_at="2026-11-23T10:00:00+00:00", event_id="2"),
            SimpleNamespace(owner_id="owner", project_id="yuki", event_kind="risk_recorded",
                            summary="Evidence verifier not yet deployed", created_at="2026-11-23T11:00:00+00:00", event_id="3"),
            SimpleNamespace(owner_id="owner", project_id="yuki", event_kind="planning_decision",
                            summary="Keep changes additive", created_at="2026-11-23T11:30:00+00:00", event_id="4"),
        )
        milestones = (
            SimpleNamespace(owner_id="owner", project_id="yuki", title="Core runtime", status="completed"),
            SimpleNamespace(owner_id="owner", project_id="yuki", title="Resume intelligence", status="open"),
        )
        report = build_project_resume_assessment(
            project_id="yuki", owner_id="owner", tasks=tasks,
            awareness_events=events, milestones=milestones, now=NOW,
        )
        self.assertEqual(report.current_goal, "Finish secure resume")
        self.assertEqual(report.progress.verified_completed, 1)
        self.assertEqual(report.progress.succeeded_unverified, 1)
        self.assertEqual(report.next_actions.ready_task_ids, ("next",))
        self.assertEqual(report.next_actions.blocked_task_ids, ("blocked",))
        self.assertEqual(report.recent_risks, ("Evidence verifier not yet deployed",))
        self.assertEqual(report.previous_decisions, ("Keep changes additive",))
        self.assertEqual(report.open_milestone_titles, ("Resume intelligence",))
        self.assertEqual(report.completed_milestone_titles, ("Core runtime",))
        self.assertIn("live task state", report.render())

    def test_resume_assessment_rejects_cross_owner_history_and_bounds_inputs(self):
        bad_event = SimpleNamespace(owner_id="other", project_id="yuki",
                                     event_kind="risk_recorded", summary="private",
                                     created_at=NOW.isoformat(), event_id="1")
        with self.assertRaises(PermissionError):
            build_project_resume_assessment(
                project_id="yuki", owner_id="owner", tasks=(),
                awareness_events=(bad_event,), now=NOW,
            )
        with self.assertRaises(ValueError):
            build_project_resume_assessment(
                project_id="yuki", owner_id="owner", tasks=(), limit=0, now=NOW,
            )

    def test_progress_requires_explicit_human_review_attestation(self):
        report = build_project_progress(
            project_id="yuki", owner_id="owner",
            tasks=(
                ProjectTaskEvidence("a", "owner", "succeeded", NOW.isoformat(), verification_ref="review:approved", human_review_verified=True),
                ProjectTaskEvidence("b", "owner", "succeeded", NOW.isoformat()),
                ProjectTaskEvidence("c", "owner", "running", NOW.isoformat()),
            ),
            now=NOW,
        )
        self.assertEqual(report.verified_completed, 1)
        self.assertEqual(report.succeeded_unverified, 1)
        self.assertAlmostEqual(report.measured_progress_percent, 100 / 3)
        self.assertIn("succeeded but not human-reviewed: 1", report.render())

    def test_ci_evidence_never_counts_as_human_reviewed_task_completion(self):
        report = build_project_progress(
            project_id="yuki", owner_id="owner",
            tasks=(
                ProjectTaskEvidence(
                    "ci-only", "owner", "succeeded", NOW.isoformat(),
                    ci_workflow_verified=True, artifacts_verified=True,
                ),
                ProjectTaskEvidence(
                    "reviewed", "owner", "succeeded", NOW.isoformat(),
                    verification_ref="review:approved", human_review_verified=True,
                    ci_workflow_verified=True, artifacts_verified=True,
                ),
            ),
            now=NOW,
        )
        self.assertEqual(report.verified_completed, 1)
        self.assertEqual(report.succeeded_unverified, 1)
        self.assertEqual(report.ci_workflow_verified, 2)
        self.assertEqual(report.artifacts_verified, 2)
        self.assertAlmostEqual(report.measured_progress_percent, 50.0)

    def test_next_task_shortlist_requires_reviewed_successful_dependencies(self):
        tasks = (
            ProjectTaskEvidence(
                "foundation", "owner", "succeeded", NOW.isoformat(),
                verification_ref="review:foundation", human_review_verified=True,
            ),
            ProjectTaskEvidence(
                "ci-only", "owner", "succeeded", NOW.isoformat(),
                verification_ref="ci:run", ci_workflow_verified=True,
            ),
            ProjectTaskEvidence("ready", "owner", "queued", NOW.isoformat(), ("foundation",)),
            ProjectTaskEvidence("blocked-review", "owner", "queued", NOW.isoformat(), ("ci-only",)),
            ProjectTaskEvidence("blocked-running", "owner", "queued", NOW.isoformat(), ("active",)),
            ProjectTaskEvidence("active", "owner", "running", NOW.isoformat()),
            ProjectTaskEvidence("free", "owner", "queued", NOW.isoformat()),
        )
        report = recommend_next_project_tasks(
            project_id="yuki", owner_id="owner", tasks=tasks, limit=5,
        )
        self.assertEqual(report.ready_task_ids, ("free", "ready"))
        self.assertEqual(report.blocked_task_ids, ("blocked-review", "blocked-running"))
        self.assertEqual(report.waiting_task_ids, ("active",))
        rendered = report.render()
        self.assertIn("approval check", rendered)
        self.assertIn("does not dispatch", rendered)

    def test_next_task_shortlist_is_owner_scoped_and_bounded(self):
        with self.assertRaises(PermissionError):
            recommend_next_project_tasks(
                project_id="p", owner_id="alice",
                tasks=(ProjectTaskEvidence("t", "bob", "queued", NOW.isoformat()),),
            )
        with self.assertRaises(ValueError):
            recommend_next_project_tasks(
                project_id="p", owner_id="alice", tasks=(), limit=0,
            )

    def test_human_review_flag_requires_a_verification_reference(self):
        with self.assertRaises(ValueError):
            build_project_progress(
                project_id="p", owner_id="o",
                tasks=(ProjectTaskEvidence(
                    "t", "o", "succeeded", NOW.isoformat(),
                    human_review_verified=True,
                ),),
                now=NOW,
            )

    def test_artifact_verification_without_workflow_is_rejected(self):
        with self.assertRaises(ValueError):
            build_project_progress(
                project_id="p", owner_id="o",
                tasks=(ProjectTaskEvidence(
                    "t", "o", "queued", NOW.isoformat(), artifacts_verified=True,
                ),),
                now=NOW,
            )

    def test_explicit_dependency_bottleneck_and_stale_age_are_labeled(self):
        report = build_project_progress(
            project_id="yuki", owner_id="owner",
            tasks=(
                ProjectTaskEvidence("build", "owner", "blocked", (NOW - timedelta(days=9)).isoformat()),
                ProjectTaskEvidence("release", "owner", "queued", NOW.isoformat(), ("build",)),
            ),
            now=NOW,
        )
        self.assertEqual(report.unresolved_dependency_task_ids, ("release",))
        self.assertEqual(report.stale_task_ids, ("build",))
        codes = {item.code for item in report.recommendations}
        self.assertIn("review_blocked_tasks", codes)
        self.assertIn("resolve_dependencies", codes)
        self.assertIn("review_stale_activity", codes)
        self.assertIn("heuristic", report.render())

    def test_missing_dependency_is_not_assumed_satisfied(self):
        report = build_project_progress(
            project_id="p", owner_id="o",
            tasks=(ProjectTaskEvidence("child", "o", "queued", NOW.isoformat(), ("not-in-snapshot",)),),
            now=NOW,
        )
        self.assertEqual(report.unresolved_dependency_task_ids, ("child",))

    def test_owner_mismatch_fails_closed(self):
        with self.assertRaises(PermissionError):
            build_project_progress(
                project_id="p", owner_id="o",
                tasks=(ProjectTaskEvidence("t", "other", "succeeded", NOW.isoformat(), verification_ref="proof"),),
                now=NOW,
            )

    def test_duplicate_ids_invalid_states_and_bounds_are_rejected(self):
        task = ProjectTaskEvidence("t", "o", "queued", NOW.isoformat())
        with self.assertRaises(ValueError):
            build_project_progress(project_id="p", owner_id="o", tasks=(task, task), now=NOW)
        with self.assertRaises(ValueError):
            build_project_progress(
                project_id="p", owner_id="o",
                tasks=(ProjectTaskEvidence("x", "o", "unknown", NOW.isoformat()),), now=NOW,
            )
        with self.assertRaises(ValueError):
            build_project_progress(project_id="p", owner_id="o", tasks=(), now=NOW, stale_after_days=0)

    def test_naive_timestamp_is_not_used_as_staleness_evidence(self):
        report = build_project_progress(
            project_id="p", owner_id="o",
            tasks=(ProjectTaskEvidence("t", "o", "running", "2020-01-01T00:00:00"),),
            now=NOW,
        )
        self.assertEqual(report.stale_task_ids, ())

    def test_unbounded_generator_is_stopped_at_limit(self):
        def endless():
            while True:
                yield ProjectTaskEvidence("same", "o", "queued", NOW.isoformat())
        with self.assertRaises(ValueError):
            build_project_progress(project_id="p", owner_id="o", tasks=endless(), now=NOW)


if __name__ == "__main__":
    unittest.main()
