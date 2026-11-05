import unittest
from dataclasses import dataclass

from automation.operation_dispatch import OperationDispatcher, OperationDispatchError
from automation.operation_providers import OperationProviderRegistry, ProviderCapabilities


@dataclass
class Operation:
    operation_id: str
    task_id: str
    idempotency_key: str
    action: str
    request_digest: str
    status: str = "prepared"
    version: int = 0


class FakeRuntime:
    def __init__(self, operation):
        self.operation = operation
        self.recorded = []
        self.resolved = []

    def _actor(self, actor):
        return actor

    def authorize_operation_dispatch(self, *, actor_id, operation_id):
        return self._authorized_operation(actor_id, operation_id, "task:operation:dispatch")

    def _authorized_operation(self, actor, operation_id, action):
        if operation_id != self.operation.operation_id or actor != "owner":
            raise PermissionError()
        return self.operation

    def mark_operation_in_flight(self, **kwargs):
        self.operation.status = "in_flight"
        self.operation.version += 1
        return self.operation

    def record_operation_outcome(self, **kwargs):
        self.recorded.append(kwargs)
        self.operation.status = kwargs["status"]
        self.operation.version += 1
        return self.operation

    def resolve_reconciled_operation(self, **kwargs):
        self.resolved.append(kwargs)
        self.operation.status = "succeeded"
        self.operation.version += 1
        return self.operation


class Provider:
    def __init__(self, response=None, error=None, lookup_response=None, lookup_error=None):
        self.response, self.error, self.keys = response, error, []
        self.lookup_response, self.lookup_error, self.lookup_keys = lookup_response, lookup_error, []

    def execute(self, action, request, *, idempotency_key):
        self.keys.append(idempotency_key)
        if self.error:
            raise self.error
        return self.response

    def lookup(self, *, idempotency_key):
        self.lookup_keys.append(idempotency_key)
        if self.lookup_error:
            raise self.lookup_error
        return self.lookup_response


def digest(request):
    import hashlib, json
    return hashlib.sha256(json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


class OperationDispatcherTests(unittest.TestCase):
    def setup_dispatcher(self, provider):
        request = {"amount": 12, "currency": "USD"}
        operation = Operation("op-1", "task-1", "stable-key", "payment.create", digest(request))
        runtime = FakeRuntime(operation)
        registry = OperationProviderRegistry()
        registry.register("payments.test", provider, ProviderCapabilities(True, True))
        return OperationDispatcher(runtime, registry), runtime, operation, request, provider

    def test_dispatch_preflight_requires_outcome_permission_before_provider_call(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider({"status": "succeeded"})
        )
        runtime.authorize_operation_dispatch = lambda **kwargs: (_ for _ in ()).throw(PermissionError())
        with self.assertRaises(PermissionError):
            dispatcher.dispatch(
                actor_id="owner", worker_id="worker-1", operation_id="op-1",
                provider_name="payments.test", request=request,
            )
        self.assertEqual(provider.keys, [])
        self.assertEqual(operation.status, "prepared")

    def test_success_passes_stable_key_and_records_success(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider({"status": "succeeded", "provider_reference": "remote-1"})
        )
        result = dispatcher.dispatch(
            actor_id="owner", worker_id="worker-1", operation_id="op-1",
            provider_name="payments.test", request=request,
        )
        self.assertEqual(provider.keys, ["stable-key"])
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(runtime.recorded[0]["status"], "succeeded")

    def test_mismatched_request_is_rejected_before_provider_call(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider({"status": "succeeded"})
        )
        with self.assertRaises(Exception):
            dispatcher.dispatch(
                actor_id="owner", worker_id="worker-1", operation_id="op-1",
                provider_name="payments.test", request={"amount": 99},
            )
        self.assertEqual(provider.keys, [])
        self.assertEqual(operation.status, "prepared")

    def test_provider_exception_becomes_reconciliation_not_retry(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider(error=TimeoutError())
        )
        with self.assertRaises(OperationDispatchError):
            dispatcher.dispatch(
                actor_id="owner", worker_id="worker-1", operation_id="op-1",
                provider_name="payments.test", request=request,
            )
        self.assertEqual(provider.keys, ["stable-key"])
        self.assertEqual(runtime.recorded[-1]["status"], "needs_reconciliation")

    def test_reconcile_confirms_success_only_with_matching_provider_identity(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider(lookup_response={
                "status": "succeeded", "idempotency_key": "stable-key",
                "provider_reference": "remote-confirmed",
            })
        )
        operation.status = "needs_reconciliation"
        operation.version = 3
        result = dispatcher.reconcile(
            actor_id="owner", operation_id="op-1", provider_name="payments.test",
        )
        self.assertEqual(provider.lookup_keys, ["stable-key"])
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(runtime.resolved[0]["provider_reference"], "remote-confirmed")

    def test_reconcile_not_found_or_pending_preserves_uncertainty(self):
        for response in (None, {"status": "pending"}, {"status": "failed"}):
            dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
                Provider(lookup_response=response)
            )
            operation.status = "needs_reconciliation"
            operation.version = 4
            result = dispatcher.reconcile(
                actor_id="owner", operation_id="op-1", provider_name="payments.test",
            )
            self.assertEqual(result.status, "needs_reconciliation")
            self.assertEqual(runtime.resolved, [])

    def test_reconcile_rejects_a_different_provider_than_original_dispatch(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider(lookup_response={
                "status": "succeeded", "idempotency_key": "stable-key",
                "provider_reference": "remote-1",
            })
        )
        operation.status = "needs_reconciliation"
        operation.provider_name = "payments.original"
        with self.assertRaises(OperationDispatchError):
            dispatcher.reconcile(
                actor_id="owner", operation_id="op-1", provider_name="payments.test",
            )
        self.assertEqual(provider.lookup_keys, [])

    def test_reconcile_rejects_mismatched_idempotency_identity(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider(lookup_response={
                "status": "succeeded", "idempotency_key": "different-key",
                "provider_reference": "remote-1",
            })
        )
        operation.status = "needs_reconciliation"
        with self.assertRaises(OperationDispatchError):
            dispatcher.reconcile(
                actor_id="owner", operation_id="op-1", provider_name="payments.test",
            )
        self.assertEqual(runtime.resolved, [])

    def test_reconcile_provider_lookup_exception_preserves_uncertainty(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider(lookup_error=TimeoutError())
        )
        operation.status = "needs_reconciliation"
        with self.assertRaises(OperationDispatchError):
            dispatcher.reconcile(
                actor_id="owner", operation_id="op-1", provider_name="payments.test",
            )
        self.assertEqual(runtime.resolved, [])

    def test_ambiguous_provider_response_is_not_success(self):
        dispatcher, runtime, operation, request, provider = self.setup_dispatcher(
            Provider({"status": "accepted"})
        )
        with self.assertRaises(OperationDispatchError):
            dispatcher.dispatch(
                actor_id="owner", worker_id="worker-1", operation_id="op-1",
                provider_name="payments.test", request=request,
            )
        self.assertEqual(runtime.recorded[-1]["status"], "needs_reconciliation")


if __name__ == "__main__":
    unittest.main()
