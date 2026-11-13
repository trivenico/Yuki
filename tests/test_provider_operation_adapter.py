"""Tests for the trusted provider operation boundary."""
import unittest

from core.operation_context import OperationExecutionContext, bind_operation_context
from core.provider_operation_adapter import ProviderOperationAdapter, ProviderOperationResult


class ProviderOperationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.context = OperationExecutionContext(
            pending_id="pending-1",
            operation_id="stable-operation-id",
            action_digest="a" * 64,
            actor="owner-1",
            session_id="session-1",
        )

    def test_forwards_stable_idempotency_key_and_immutable_identity(self):
        calls = []

        def execute_provider(**kwargs):
            calls.append(kwargs)
            return {"provider_receipt": "receipt-1"}

        adapter = ProviderOperationAdapter("acme_payments", execute_provider)
        with bind_operation_context(self.context):
            result = adapter({"amount": 125, "currency": "USD"})

        self.assertIsInstance(result, ProviderOperationResult)
        self.assertEqual(result.provider_id, "acme_payments")
        self.assertEqual(result.operation_id, self.context.operation_id)
        self.assertEqual(result.action_digest, self.context.action_digest)
        self.assertEqual(calls[0]["idempotency_key"], self.context.operation_id)
        self.assertEqual(calls[0]["operation_context"], self.context)
        self.assertEqual(calls[0]["request"], {"amount": 125, "currency": "USD"})

    def test_refuses_execution_without_approved_durable_context(self):
        adapter = ProviderOperationAdapter("acme_payments", lambda **_: self.fail("must not call"))
        with self.assertRaises(PermissionError):
            adapter({"amount": 1})

    def test_provider_identity_is_host_configured_and_validated(self):
        with self.assertRaises(ValueError):
            ProviderOperationAdapter("../provider", lambda **_: None)
        with self.assertRaises(TypeError):
            ProviderOperationAdapter("acme_payments", None)

    def test_rejects_non_json_and_oversized_requests_before_provider_call(self):
        calls = []
        adapter = ProviderOperationAdapter("acme_payments", lambda **kwargs: calls.append(kwargs))
        with bind_operation_context(self.context):
            with self.assertRaises(ValueError):
                adapter({"bad": object()})
            with self.assertRaises(ValueError):
                adapter({"data": "x" * 21_000})
        self.assertEqual(calls, [])

    def test_provider_exception_is_propagated_for_unknown_outcome_recovery(self):
        def fail(**_kwargs):
            raise TimeoutError("provider response lost")

        adapter = ProviderOperationAdapter("acme_payments", fail)
        with bind_operation_context(self.context):
            with self.assertRaises(TimeoutError):
                adapter({"amount": 1})


if __name__ == "__main__":
    unittest.main()
