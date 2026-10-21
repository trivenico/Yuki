import unittest
from unittest.mock import Mock

from core.protected_operation import run_protected_operation
from core.release_gate import ReleaseGateDecision

SHA = "a" * 40
TARGET = dict(target_repository="trivenico/Yuki", target_pull_request=74, target_head_sha=SHA)


def decision(*, allowed=True, persisted=True, repository="trivenico/yuki", pr=74, sha=SHA):
    return ReleaseGateDecision(
        decision="ready-for-required-checks" if allowed else "block",
        reasons=() if allowed else ("unit_test_block",),
        repository=repository,
        pull_request=pr,
        expected_head_sha=sha,
        evidence_id=1 if persisted else None,
        audit_persisted=persisted,
    )


class ProtectedOperationTests(unittest.TestCase):
    def run_gate(self, evaluate_gate, operation, **target):
        return run_protected_operation(
            evaluate_gate=evaluate_gate, operation=operation, **(target or TARGET)
        )

    def test_operation_runs_only_after_persisted_allow_for_exact_target(self):
        operation = Mock(return_value="deployed-artifact")
        result = self.run_gate(lambda: decision(), operation)
        self.assertTrue(result.allowed)
        self.assertEqual(result.operation_result, "deployed-artifact")
        operation.assert_called_once_with()

    def test_blocked_decision_never_calls_operation(self):
        operation = Mock()
        result = self.run_gate(lambda: decision(allowed=False), operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_blocked")
        operation.assert_not_called()

    def test_unpersisted_allow_never_calls_operation(self):
        operation = Mock()
        result = self.run_gate(lambda: decision(persisted=False), operation)
        self.assertFalse(result.allowed)
        operation.assert_not_called()

    def test_gate_exception_fails_closed(self):
        operation = Mock()
        def broken_gate():
            raise RuntimeError("secret-bearing error")
        result = self.run_gate(broken_gate, operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_evaluation_failed")
        operation.assert_not_called()

    def test_malformed_gate_result_fails_closed(self):
        operation = Mock()
        result = self.run_gate(lambda: {"allowed": True}, operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_returned_invalid_decision")
        operation.assert_not_called()

    def test_wrong_repository_blocks(self):
        operation = Mock()
        result = self.run_gate(lambda: decision(repository="attacker/other"), operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_target_mismatch")
        operation.assert_not_called()

    def test_wrong_pull_request_blocks(self):
        operation = Mock()
        result = self.run_gate(lambda: decision(pr=75), operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_target_mismatch")
        operation.assert_not_called()

    def test_wrong_commit_sha_blocks(self):
        operation = Mock()
        result = self.run_gate(lambda: decision(sha="b" * 40), operation)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "release_gate_target_mismatch")
        operation.assert_not_called()

    def test_invalid_target_fails_closed_before_gate_evaluation(self):
        operation = Mock()
        gate = Mock(return_value=decision())
        result = self.run_gate(gate, operation, target_head_sha="../bad")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "protected_operation_target_invalid")
        gate.assert_not_called()
        operation.assert_not_called()

    def test_operation_failure_is_not_misreported_as_success(self):
        operation = Mock(side_effect=OSError("disk failed"))
        with self.assertRaises(OSError):
            self.run_gate(lambda: decision(), operation)
        operation.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
