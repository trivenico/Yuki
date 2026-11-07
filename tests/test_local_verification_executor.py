import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from automation.local_verification_executor import (
    LocalVerificationExecutor, VerificationCommand,
)


class LocalVerificationExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.script = sys.executable

    def tearDown(self):
        self.tmp.cleanup()

    def executor(self, commands, *, cap=8192):
        return LocalVerificationExecutor(
            self.root, self.root / "receipts.sqlite3", commands,
            max_output_bytes=cap,
        )

    def test_allowlisted_command_runs_without_shell_and_persists_receipt(self):
        executor = self.executor({
            "unit-tests": VerificationCommand(
                "unit-tests",
                (self.script, "-c", "import os; print(os.getcwd()); print('tests passed')"),
                10,
            )
        })
        receipt = executor.run("unit-tests")
        self.assertEqual(receipt.status, "passed")
        self.assertEqual(receipt.exit_code, 0)
        self.assertEqual(receipt.workspace, str(self.root.resolve()))
        self.assertIn("tests passed", receipt.output_excerpt)
        self.assertEqual(receipt.output_sha256, hashlib.sha256(
            receipt.output_excerpt.encode()
        ).hexdigest())
        stored = executor.get(receipt.execution_id)
        self.assertEqual(stored["status"], "passed")
        self.assertEqual(stored["command_id"], "unit-tests")

    def test_unknown_command_is_rejected_before_process_launch(self):
        executor = self.executor({
            "allowed": VerificationCommand("allowed", (self.script, "-c", "pass"))
        })
        with self.assertRaises(PermissionError):
            executor.run("arbitrary-shell")

    def test_nonzero_exit_is_failed(self):
        executor = self.executor({
            "failing-tests": VerificationCommand(
                "failing-tests", (self.script, "-c", "raise SystemExit(7)")
            )
        })
        receipt = executor.run("failing-tests")
        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.exit_code, 7)

    def test_timeout_terminates_command(self):
        executor = self.executor({
            "slow-tests": VerificationCommand(
                "slow-tests", (self.script, "-c", "import time; time.sleep(5)"), 0.1
            )
        })
        receipt = executor.run("slow-tests")
        self.assertEqual(receipt.status, "timed_out")
        self.assertTrue(receipt.timed_out)

    def test_output_cap_terminates_command_and_records_truncation(self):
        executor = self.executor({
            "noisy-tests": VerificationCommand(
                "noisy-tests",
                (self.script, "-c", "import sys; sys.stdout.write('x' * 1000000); sys.stdout.flush()"),
                10,
            )
        }, cap=1024)
        receipt = executor.run("noisy-tests")
        self.assertEqual(receipt.status, "output_limited")
        self.assertTrue(receipt.output_limited)
        self.assertLessEqual(receipt.output_bytes, 1024)

    def test_configuration_rejects_invalid_ids_and_limits(self):
        with self.assertRaises(ValueError):
            VerificationCommand("bad command", (self.script, "-c", "pass")).validate()
        with self.assertRaises(ValueError):
            VerificationCommand("valid", (self.script,), 0).validate()
        with self.assertRaises(ValueError):
            self.executor({
                "valid": VerificationCommand("valid", (self.script,))
            }, cap=1)

    def test_only_successful_execution_receipt_can_become_passed_check(self):
        executor = self.executor({
            "passing": VerificationCommand("passing", (self.script, "-c", "pass")),
            "failing": VerificationCommand("failing", (self.script, "-c", "raise SystemExit(2)")),
        })
        passing = executor.run("passing")
        failed = executor.run("failing")
        passing_check = executor.to_verification_check(
            passing, check_name="tests", reviewer_agent_id="reviewer-tests",
        )
        failed_check = executor.to_verification_check(
            failed, check_name="tests", reviewer_agent_id="reviewer-tests",
        )
        self.assertEqual(passing_check.status, "passed")
        self.assertEqual(passing_check.evidence_reference, executor.evidence_reference(passing))
        self.assertEqual(failed_check.status, "failed")


if __name__ == "__main__":
    unittest.main()
