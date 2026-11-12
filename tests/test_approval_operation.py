import unittest

from core.approval_operation import ApprovalOperation, canonical_operation_payload
from core.tool_policy import PolicyRequest, Risk, argument_digest


class ApprovalOperationTests(unittest.TestCase):
    def test_payload_digest_is_stable_across_mapping_order(self):
        first = {"recipient": "owner@example.test", "body": "Review this"}
        second = {"body": "Review this", "recipient": "owner@example.test"}
        operation = ApprovalOperation.from_payload(
            operation_type="message.send",
            capability="messages:send",
            target="account:primary",
            payload=first,
        )
        self.assertTrue(operation.verify_payload(second))

    def test_payload_changes_invalidate_operation(self):
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write",
            capability="workspace:write",
            target="workspace:project-a",
            payload={"path": "README.md", "content": "approved"},
        )
        self.assertFalse(operation.verify_payload({
            "path": "README.md", "content": "different",
        }))

    def test_policy_request_must_match_capability_target_and_payload_digest(self):
        payload = {"path": "README.md", "content": "approved"}
        operation = ApprovalOperation.from_payload(
            operation_type="workspace.write",
            capability="workspace:write",
            target="workspace:project-a",
            payload=payload,
        )
        request = PolicyRequest(
            principal_id="owner-1", trigger="durable_task",
            capability="workspace:write", target="workspace:project-a",
            argument_sha256=argument_digest(payload), risk=Risk.HIGH,
            run_id="goal-1", step_id="task-1",
        )
        self.assertTrue(operation.verify_policy_request(request))
        changed = PolicyRequest(
            principal_id="owner-1", trigger="durable_task",
            capability="workspace:write", target="workspace:project-a",
            argument_sha256=argument_digest({"path": "README.md", "content": "other"}),
            risk=Risk.HIGH, run_id="goal-1", step_id="task-1",
        )
        self.assertFalse(operation.verify_policy_request(changed))

    def test_rejects_non_finite_and_oversized_payloads(self):
        with self.assertRaises(ValueError):
            canonical_operation_payload({"amount": float("nan")})
        with self.assertRaises(ValueError):
            canonical_operation_payload({"content": "x" * 256_001})

    def test_rejects_unsupported_schema_and_invalid_digest(self):
        with self.assertRaises(ValueError):
            ApprovalOperation(2, "message.send", "messages:send", "account:primary", "a" * 64)
        with self.assertRaises(ValueError):
            ApprovalOperation(1, "message.send", "messages:send", "account:primary", "not-a-digest")


if __name__ == "__main__":
    unittest.main()
