import tempfile
import unittest
from pathlib import Path

from automation.approved_workspace_changes import ApprovedTestRunner
from automation.authorized_read_tools import ToolAuthorizationError, ToolPrincipal
from automation.regression_test_planner import RegressionTestCandidate, RegressionTestPlan
from automation.regression_test_executor import run_approved_regression_candidate


class RegressionTestExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tests_dir = self.root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sample.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_ok(self): self.assertEqual(3 * 3, 9)\n",
            encoding="utf-8",
        )
        self.events = []
        self.approvals = []
        self.principal = ToolPrincipal("owner", frozenset({"tests:execute"}))
        self.runner = ApprovedTestRunner(
            self.root,
            trusted_principal=lambda principal: principal.actor_id == "owner",
            approve_run=lambda actor, run_id, digest: (
                self.approvals.append((actor, run_id, digest)) or True
            ),
            audit=self.events.append,
            allowed_test_modules=("tests.test_sample",),
            timeout_seconds=10,
        )
        self.plan = RegressionTestPlan(
            candidates=(RegressionTestCandidate(
                "tests/test_sample.py", "high", ("direct importer",)
            ),),
            full_suite_recommended=False,
            full_suite_reasons=(),
        )

    def test_runs_exact_candidate_through_existing_approval_gate(self):
        execution = run_approved_regression_candidate(
            self.plan, self.runner, principal=self.principal,
            test_path="tests/test_sample.py",
        )
        self.assertTrue(execution.tests_executed)
        self.assertTrue(execution.passed)
        self.assertEqual(execution.result.target, "tests.test_sample")
        self.assertEqual(len(self.approvals), 1)
        self.assertEqual(self.events[-1]["outcome"], "succeeded")

    def test_rejects_non_candidate_without_asking_for_approval(self):
        with self.assertRaises(ToolAuthorizationError):
            run_approved_regression_candidate(
                self.plan, self.runner, principal=self.principal,
                test_path="tests/test_other.py",
            )
        self.assertEqual(self.approvals, [])

    def test_rejects_candidate_not_in_runner_allowlist(self):
        runner = ApprovedTestRunner(
            self.root,
            trusted_principal=lambda principal: True,
            approve_run=lambda *_args: True,
            audit=self.events.append,
            allowed_test_modules=("tests.other",),
        )
        with self.assertRaises(ToolAuthorizationError):
            run_approved_regression_candidate(
                self.plan, runner, principal=self.principal,
                test_path="tests/test_sample.py",
            )

    def test_runner_denial_prevents_execution(self):
        runner = ApprovedTestRunner(
            self.root,
            trusted_principal=lambda principal: True,
            approve_run=lambda *_args: False,
            audit=self.events.append,
            allowed_test_modules=("tests.test_sample",),
        )
        with self.assertRaises(ToolAuthorizationError):
            run_approved_regression_candidate(
                self.plan, runner, principal=self.principal,
                test_path="tests/test_sample.py",
            )
        self.assertFalse(any(event["outcome"] == "started" for event in self.events))


if __name__ == "__main__":
    unittest.main()
