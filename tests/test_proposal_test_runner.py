import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from core.proposal_test_runner import (
    ProposalTestRunError,
    run_proposal_checks,
)
from core.sandbox_executor import SandboxResult
from core.sandbox_policy import SandboxPolicy


class ProposalTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / "jobs"
        self.parent.mkdir()
        self.workspace = self.parent / "yuki-improvement-test"
        self.workspace.mkdir()
        (self.workspace / "test_example.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    @patch("core.proposal_test_runner.execute_docker_sandbox")
    def test_runs_allowed_checks_in_order(self, execute):
        execute.return_value = SandboxResult(0, "ok", "", False, False, True)
        results = run_proposal_checks(
            workspace=self.workspace, parent_dir=self.parent,
            policy=SandboxPolicy(), image="python:3.12",
            commands=[["python", "-m", "pytest", "-q"], ["ruff", "check", "."]],
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(execute.call_count, 2)

    @patch("core.proposal_test_runner.execute_docker_sandbox")
    def test_stops_after_first_failed_check(self, execute):
        execute.return_value = SandboxResult(1, "", "failure", False, False, True)
        results = run_proposal_checks(
            workspace=self.workspace, parent_dir=self.parent,
            policy=SandboxPolicy(), image="python:3.12",
            commands=[["pytest", "-q"], ["ruff", "check", "."]],
        )
        self.assertEqual(len(results), 1)
        execute.assert_called_once()

    @patch("core.proposal_test_runner.execute_docker_sandbox")
    def test_rejects_shell_and_inline_code_before_execution(self, execute):
        for command in (["sh", "-c", "echo unsafe"], ["python", "-c", "print(1)"],
                        ["custom-executable", "arg"]):
            with self.subTest(command=command):
                with self.assertRaises(ProposalTestRunError):
                    run_proposal_checks(
                        workspace=self.workspace, parent_dir=self.parent,
                        policy=SandboxPolicy(), image="python:3.12", commands=[command],
                    )
        execute.assert_not_called()

    @patch("core.proposal_test_runner.execute_docker_sandbox")
    def test_rejects_workspace_outside_managed_parent(self, execute):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        with self.assertRaises(ProposalTestRunError):
            run_proposal_checks(
                workspace=outside, parent_dir=self.parent,
                policy=SandboxPolicy(), image="python:3.12", commands=[["pytest", "-q"]],
            )
        execute.assert_not_called()

    @patch("core.proposal_test_runner.execute_docker_sandbox")
    def test_timeout_stops_remaining_checks(self, execute):
        execute.return_value = SandboxResult(-9, "", "", True, False, True)
        results = run_proposal_checks(
            workspace=self.workspace, parent_dir=self.parent,
            policy=SandboxPolicy(), image="python:3.12",
            commands=[["pytest", "-q"], ["ruff", "check", "."]],
        )
        self.assertEqual(len(results), 1)
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
