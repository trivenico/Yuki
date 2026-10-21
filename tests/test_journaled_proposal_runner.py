import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.execution_journal import SQLiteExecutionJournal
from core.journaled_proposal_runner import (
    JournaledProposalRunError,
    run_journaled_proposal_checks,
)
from core.proposal_test_runner import ProposalTestRunError
from core.sandbox_executor import SandboxResult
from core.sandbox_policy import SandboxPolicy


class JournaledProposalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / "jobs"
        self.parent.mkdir()
        self.workspace = self.parent / "yuki-improvement-test"
        self.workspace.mkdir()
        (self.workspace / "test_sample.py").write_text("def test_ok(): pass\n", encoding="utf-8")
        self.journal = SQLiteExecutionJournal(str(self.root / "journal.sqlite3"))

    def run_checks(self, execution_id="run-1", commands=(("pytest", "-q"),)):
        return run_journaled_proposal_checks(
            journal=self.journal,
            execution_id=execution_id,
            proposal_sha256="a" * 64,
            workspace=self.workspace,
            parent_dir=self.parent,
            policy=SandboxPolicy(),
            image="python:3.12",
            commands=commands,
        )

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_success_is_persisted_with_cleanup_confirmation(self, run):
        run.return_value = (SandboxResult(0, "ok", "", False, False, True),)
        result = self.run_checks()
        self.assertEqual(result.execution.state, "succeeded")
        self.assertTrue(result.execution.cleanup_confirmed)
        self.assertEqual(self.journal.get("run-1").state, "succeeded")

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_failed_check_is_persisted_and_not_replayed(self, run):
        run.return_value = (SandboxResult(1, "", "failed", False, False, True),)
        result = self.run_checks()
        self.assertEqual(result.execution.state, "failed")
        with self.assertRaisesRegex(JournaledProposalRunError, "replay is forbidden"):
            self.run_checks()
        run.assert_called_once()

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_executor_exception_requires_cleanup_reconciliation(self, run):
        run.side_effect = ProposalTestRunError("cleanup uncertain")
        with self.assertRaisesRegex(JournaledProposalRunError, "reconciliation is required"):
            self.run_checks()
        record = self.journal.get("run-1")
        self.assertEqual(record.state, "cleanup_required")
        self.assertFalse(record.cleanup_confirmed)

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_timeout_is_persisted_as_timed_out(self, run):
        run.return_value = (SandboxResult(-9, "partial", "", True, False, True),)
        result = self.run_checks()
        self.assertEqual(result.execution.state, "timed_out")

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_invalid_command_never_creates_journal_or_launches(self, run):
        with self.assertRaises(JournaledProposalRunError):
            self.run_checks(commands=(("python", "-c", "print(1)"),))
        run.assert_not_called()
        with self.assertRaisesRegex(Exception, "not found"):
            self.journal.get("run-1")

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_aggregate_output_is_bounded_before_journal_persistence(self, run):
        huge = "é" * 50000
        run.return_value = (
            SandboxResult(0, huge, huge, False, False, True),
            SandboxResult(0, huge, huge, False, False, True),
        )
        result = self.run_checks(commands=(("pytest", "-q"), ("ruff", "check", ".")))
        payload = json.loads(result.execution.result_json)
        self.assertTrue(payload["journal_output_truncated"])
        self.assertLessEqual(len(result.execution.result_json.encode("utf-8")), 65536)
        self.assertTrue(any(item["output_truncated"] for item in payload["checks"]))

    @patch("core.journaled_proposal_runner.run_proposal_checks")
    def test_too_many_checks_are_rejected_before_journaling(self, run):
        commands = tuple(("pytest", "-q") for _ in range(33))
        with self.assertRaisesRegex(JournaledProposalRunError, "Preflight validation failed"):
            self.run_checks(commands=commands)
        run.assert_not_called()
        with self.assertRaisesRegex(Exception, "not found"):
            self.journal.get("run-1")


if __name__ == "__main__":
    unittest.main()
