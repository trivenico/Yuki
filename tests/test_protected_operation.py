import unittest
from unittest.mock import Mock

from core.protected_operation import run_protected_operation
from core.release_gate import ReleaseGateDecision

SHA = "a" * 40


def decision(*, allowed=True, persisted=True):
    return ReleaseGateDecision(
        decision="ready-for-required-checks" if allowed else "block",
        reasons=() if allowed else ("unit_test_block",),
        repository="trivenico/yuki",
        pull_request=74,
        expected_head_sha=SHA,
        evidence_id=1 if persisted else None,
        audit_persisted=persisted,
    )


class ProtectedOperationTests(unittest.TestCase):
    def test_operation_runs_only_after_persisted_allow(self):
        operation = Mock(return_value="deployed-artifact")
        result = run_protected_operation(
            evaluate_gate=lambda: decision(), operation=operation
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.operation_result, "deployed-artifact")
        operation.assert_called_once_with()

    def test_blocked_decision_never_calls_operation(self):
        operation = Mock()
        result = run_protected_operation(
            evaluate_gate=lambda: decision(allowed=False), operation=operation
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_blocked")
        operation.assert_not_called()

    def test_unpersisted_allow_never_calls_operation(self):
        operation = Mock()
        result = run_protected_operation(
            evaluate_gate=lambda: decision(persisted=False), operation=operation
        )
        self.assertFalse(result.allowed)
        operation.assert_not_called()

    def test_gate_exception_fails_closed(self):
        operation = Mock()
        def broken_gate():
            raise RuntimeError("secret-bearing error")
        result = run_protected_operation(evaluate_gate=broken_gate, operation=operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_evaluation_failed")
        operation.assert_not_called()

    def test_malformed_gate_result_fails_closed(self):
        operation = Mock()
        result = run_protected_operation(
            evaluate_gate=lambda: {"allowed": True}, operation=operation
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_returned_invalid_decision")
        operation.assert_not_called()

    def test_operation_failure_is_not_misreported_as_success(self):
        operation = Mock(side_effect=OSError("disk failed"))
        with self.assertRaises(OSError):
            run_protected_operation(evaluate_gate=lambda: decision(), operation=operation)
        operation.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
