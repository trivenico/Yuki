"""Integration tests for durable governed provider dispatch."""
import tempfile
import unittest
from pathlib import Path

from providers.agent_adapter import ProviderAgentAdapter
from providers.durable_provider_governance import SQLiteProviderGovernanceStore
from providers.durable_governed_provider_executor import (
    DurableGovernedProviderExecutor, DurableProviderAdmissionRejected,
)
from providers.provider_execution import ProviderCancelled
from providers.provider_gateway import ProviderGateway


class LocalInferenceEngine:
    privacy_class = "local"

    def __init__(self, *, failure=None):
        self.failure = failure
        self.calls = 0

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls += 1
        if self.failure == "cancel":
            raise ProviderCancelled("cancelled")
        if self.failure == "error":
            raise RuntimeError("transport failure")
        return {"text": "ok", "tool_calls": {"tool_calls": []}}


class DurableGovernedExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "governance.sqlite3")
        self.store = SQLiteProviderGovernanceStore(
            self.path, max_global_concurrent=2, max_provider_concurrent=2,
            max_agent_concurrent=1, max_agent_requests_per_window=20,
            lease_timeout_seconds=120,
        )
        self.request = {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [], "tool_choice": "none",
        }

    def build(self, engine, agent="assistant", store=None):
        adapter = ProviderGateway.from_adapter(ProviderAgentAdapter(engine))
        return DurableGovernedProviderExecutor(
            provider_id=adapter.provider_id, agent_id=agent, adapter=adapter,
            governance_store=store or self.store,
        )

    def test_success_records_outcome_and_releases_durable_capacity(self):
        engine = LocalInferenceEngine()
        execute = self.build(engine)
        self.assertEqual(execute(**self.request)["text"], "ok")
        snapshot = self.store.snapshot(provider_id="localinferenceengine", agent_id="assistant")
        self.assertEqual(snapshot["global_active_requests"], 0)
        self.assertEqual(snapshot["circuit_state"], "closed")
        self.assertEqual([e["event_type"] for e in reversed(
            self.store.list_events(provider_id="localinferenceengine")
        )], ["admitted", "succeeded"])

    def test_provider_exception_is_persisted_and_opens_circuit(self):
        execute = self.build(LocalInferenceEngine(failure="error"))
        with self.assertRaisesRegex(RuntimeError, "transport"):
            execute(**self.request)
        snapshot = self.store.snapshot(provider_id="localinferenceengine", agent_id="assistant")
        self.assertEqual(snapshot["global_active_requests"], 0)
        self.assertEqual(snapshot["circuit_state"], "open")
        with self.assertRaises(DurableProviderAdmissionRejected):
            execute(**self.request)

    def test_cancellation_releases_lease_without_failure_penalty(self):
        execute = self.build(LocalInferenceEngine(failure="cancel"))
        with self.assertRaises(ProviderCancelled):
            execute(**self.request)
        snapshot = self.store.snapshot(provider_id="localinferenceengine", agent_id="assistant")
        self.assertEqual(snapshot["global_active_requests"], 0)
        self.assertEqual(snapshot["circuit_state"], "closed")

    def test_second_store_instance_observes_admission_and_limits(self):
        engine = LocalInferenceEngine()
        adapter = ProviderGateway.from_adapter(ProviderAgentAdapter(engine))
        held = self.store.acquire(provider_id=adapter.provider_id, agent_id="assistant")
        second_store = SQLiteProviderGovernanceStore(
            self.path, max_global_concurrent=2, max_provider_concurrent=2,
            max_agent_concurrent=1, max_agent_requests_per_window=20,
            lease_timeout_seconds=120,
        )
        execute = self.build(engine, store=second_store)
        with self.assertRaises(DurableProviderAdmissionRejected):
            execute(**self.request)
        self.assertEqual(engine.calls, 0)
        self.store.release(held.lease_id)

    def test_invalid_request_is_rejected_before_durable_quota(self):
        execute = self.build(LocalInferenceEngine())
        with self.assertRaises(ValueError):
            execute(messages=[], tools=[], tool_choice="none")
        snapshot = self.store.snapshot(provider_id="localinferenceengine", agent_id="assistant")
        self.assertEqual(snapshot["agent_requests_in_window"], 0)
        self.assertEqual(snapshot["global_active_requests"], 0)

    def test_executor_idempotency_deduplicates_without_second_provider_call(self):
        from providers.durable_governed_provider_executor import DurableProviderDuplicate
        engine = LocalInferenceEngine()
        execute = self.build(engine)
        result = execute(**self.request, _idempotency_key="conversation-1:turn-7")
        self.assertEqual(result["text"], "ok")
        with self.assertRaises(DurableProviderDuplicate) as caught:
            execute(**self.request, _idempotency_key="conversation-1:turn-7")
        self.assertEqual(caught.exception.status, "succeeded")
        self.assertEqual(engine.calls, 1)

    def test_executor_rejects_idempotency_key_reuse_with_changed_request(self):
        execute = self.build(LocalInferenceEngine())
        execute(**self.request, _idempotency_key="operation-99")
        changed = dict(self.request)
        changed["messages"] = [{"role": "user", "content": "different"}]
        with self.assertRaisesRegex(DurableProviderAdmissionRejected, "idempotency_key_conflict"):
            execute(**changed, _idempotency_key="operation-99")

    def test_non_json_request_cannot_use_idempotency_fingerprint(self):
        execute = self.build(LocalInferenceEngine())
        request = dict(self.request)
        request["extra"] = object()
        with self.assertRaisesRegex(ValueError, "canonical_json"):
            execute(**request, _idempotency_key="operation-100")


    def test_native_idempotency_contract_forwards_key_only_when_declared(self):
        class NativeEngine(LocalInferenceEngine):
            idempotency_mode = "native"
            def generate_chat(self, *, messages, tools, tool_choice, idempotency_key):
                self.last_key = idempotency_key
                return super().generate_chat(messages=messages, tools=tools, tool_choice=tool_choice)

        engine = NativeEngine()
        execute = self.build(engine)
        execute(**self.request, _idempotency_key="native-op-1")
        self.assertEqual(engine.last_key, "native-op-1")

    def test_unsupported_provider_does_not_receive_internal_idempotency_key(self):
        engine = LocalInferenceEngine()
        execute = self.build(engine)
        execute(**self.request, _idempotency_key="local-dedupe-1")
        self.assertEqual(engine.calls, 1)

    def test_lease_timeout_must_exceed_adapter_timeout(self):
        engine = LocalInferenceEngine()
        adapter = ProviderGateway.from_adapter(ProviderAgentAdapter(engine))
        short_lease = SQLiteProviderGovernanceStore(
            self.path, lease_timeout_seconds=10,
        )
        with self.assertRaisesRegex(ValueError, "lease_timeout"):
            DurableGovernedProviderExecutor(
                provider_id=adapter.provider_id, agent_id="assistant",
                adapter=adapter, governance_store=short_lease,
            )


if __name__ == "__main__":
    unittest.main()
