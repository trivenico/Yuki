import unittest

from automation.github_actions_evidence import (
    GitHubActionsEvidenceAdapter,
    GitHubEvidenceError,
)


class GitHubActionsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.mapping = {
            "unit_tests": ("Yuki quality gates",),
            "integration_tests": ("Phase 93 engineering workflow",),
            "static_analysis": ("Phase 82 capability contract gate",),
            "dependency_audit": ("Dependency manifest audit",),
            "secret_scan": ("Secret scanning",),
            "security_tests": ("Built-in authentication security tests",),
        }
        self.runs = [
            {
                "id": i + 100, "name": names[0], "head_sha": self.sha,
                "event": "pull_request", "status": "completed",
                "conclusion": "success", "run_number": 1, "run_attempt": 1,
                "updated_at": "2026-11-01T10:00:00Z",
            }
            for i, names in enumerate(self.mapping.values())
        ]
        self.adapter = GitHubActionsEvidenceAdapter(
            lambda sha: self.runs, workflow_map=self.mapping
        )

    def test_builds_six_commit_bound_checks_from_successful_runs(self):
        report = self.adapter.build_report(self.sha)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks), 6)
        self.assertTrue(all(item.commit_sha == self.sha for item in report.checks))
        self.assertTrue(all(item.evidence_id.startswith("gh-") for item in report.checks))

    def test_rejects_stale_commit_runs(self):
        for run in self.runs:
            run["head_sha"] = "b" * 40
        with self.assertRaises(GitHubEvidenceError):
            self.adapter.build_report(self.sha)

    def test_rejects_failed_latest_run_even_if_older_run_succeeded(self):
        failed = dict(self.runs[0], id=999, run_number=2, conclusion="failure")
        self.runs.append(failed)
        with self.assertRaises(GitHubEvidenceError):
            self.adapter.build_report(self.sha)

    def test_rejects_in_progress_run(self):
        self.runs[1]["status"] = "in_progress"
        with self.assertRaises(GitHubEvidenceError):
            self.adapter.build_report(self.sha)

    def test_rejects_wrong_event(self):
        self.runs[2]["event"] = "schedule"
        with self.assertRaises(GitHubEvidenceError):
            self.adapter.build_report(self.sha)

    def test_rejects_missing_required_category(self):
        self.runs.pop()
        with self.assertRaises(GitHubEvidenceError):
            self.adapter.build_report(self.sha)

    def test_fetch_failure_fails_closed(self):
        adapter = GitHubActionsEvidenceAdapter(
            lambda sha: (_ for _ in ()).throw(OSError()),
            workflow_map=self.mapping,
        )
        with self.assertRaises(GitHubEvidenceError):
            adapter.build_report(self.sha)

    def test_rejects_invalid_expected_commit(self):
        with self.assertRaises(GitHubEvidenceError):
            self.adapter.build_report("not-a-sha")

    def test_rejects_incomplete_workflow_mapping(self):
        with self.assertRaises(ValueError):
            GitHubActionsEvidenceAdapter(lambda sha: [], workflow_map={"unit_tests": ("x",)})


if __name__ == "__main__":
    unittest.main()
