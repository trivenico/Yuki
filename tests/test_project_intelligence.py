import unittest
from datetime import datetime, timedelta, timezone

from core.project_intelligence import ProjectTaskEvidence, build_project_progress


NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


class ProjectIntelligenceTests(unittest.TestCase):
    def test_progress_requires_explicit_verification_reference(self):
        report = build_project_progress(
            project_id="yuki", owner_id="owner",
            tasks=(
                ProjectTaskEvidence("a", "owner", "succeeded", NOW.isoformat(), verification_ref="ci:run-42"),
                ProjectTaskEvidence("b", "owner", "succeeded", NOW.isoformat()),
                ProjectTaskEvidence("c", "owner", "running", NOW.isoformat()),
            ),
            now=NOW,
        )
        self.assertEqual(report.verified_completed, 1)
        self.assertEqual(report.succeeded_unverified, 1)
        self.assertAlmostEqual(report.measured_progress_percent, 100 / 3)
        self.assertIn("succeeded but unverified: 1", report.render())

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
