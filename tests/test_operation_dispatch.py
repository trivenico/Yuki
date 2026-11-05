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

    def _actor(self, actor):
        return actor

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


class Provider:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.keys = response, error, []

    def execute(self, action, request, *, idempotency_key):
        self.keys.append(idempotency_key)
        if self.error:
            raise self.error
        return self.response

    def lookup(self, *, idempotency_key):
        return None


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
