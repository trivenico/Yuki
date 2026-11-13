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

        adapter = ProviderOperationAdapter("acme_payments", execute_provider, lambda _context, _provider: None)
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
        adapter = ProviderOperationAdapter("acme_payments", lambda **_: self.fail("must not call"), lambda _context, _provider: None)
        with self.assertRaises(PermissionError):
            adapter({"amount": 1})

    def test_provider_identity_is_host_configured_and_validated(self):
        with self.assertRaises(ValueError):
            ProviderOperationAdapter("../provider", lambda **_: None, lambda _context, _provider: None)
        with self.assertRaises(TypeError):
            ProviderOperationAdapter("acme_payments", None, lambda _context, _provider: None)

    def test_rejects_non_json_and_oversized_requests_before_provider_call(self):
        calls = []
        adapter = ProviderOperationAdapter("acme_payments", lambda **kwargs: calls.append(kwargs), lambda _context, _provider: None)
        with bind_operation_context(self.context):
            with self.assertRaises(ValueError):
                adapter({"bad": object()})
            with self.assertRaises(ValueError):
                adapter({"data": "x" * 21_000})
        self.assertEqual(calls, [])

    def test_durable_provider_binding_occurs_before_provider_side_effect(self):
        events = []
        adapter = ProviderOperationAdapter(
            "acme_payments",
            lambda **kwargs: events.append(("execute", kwargs["idempotency_key")),
            lambda context, provider: events.append(("bind", context.operation_id, provider)),
        )
        with bind_operation_context(self.context):
            adapter({"amount": 1})
        self.assertEqual(events[0], ("bind", self.context.operation_id, "acme_payments"))
        self.assertEqual(events[1], ("execute", self.context.operation_id))

    def test_provider_exception_is_propagated_for_unknown_outcome_recovery(self):
        def fail(**_kwargs):
            raise TimeoutError("provider response lost")

        adapter = ProviderOperationAdapter("acme_payments", fail, lambda _context, _provider: None)
        with bind_operation_context(self.context):
            with self.assertRaises(TimeoutError):
                adapter({"amount": 1})


if __name__ == "__main__":
    unittest.main()
