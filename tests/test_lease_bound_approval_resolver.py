"""Regression tests for approval resolution fenced by the active task claim."""
import unittest
from types import SimpleNamespace
from core.lease_bound_approval_resolver import LeaseBoundApprovalResolver


class FakeEngine:
    def __init__(self):
        self.current = True
        self.calls = 0
        self.raise_on_validate = False

    def validate_lease(self, task_id, worker_id, lease_token):
        self.calls += 1
        if self.raise_on_validate:
            raise RuntimeError("storage unavailable")
        return self.current


class LeaseBoundApprovalResolverTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.context = SimpleNamespace(principal_id="owner", trigger="durable_task", goal_id="goal-1", task_id="task-1")
        self.token = "t" * 48
        self.credential = "a" * 48

    def make_resolver(self, callback):
        return LeaseBoundApprovalResolver(
            self.engine, worker_id="worker-1", resolver=callback
        )

    def test_returns_credential_when_same_claim_survives_resolution(self):
        resolver = self.make_resolver(lambda context, skill: self.credential)
        self.assertEqual(
            resolver.resolve(self.context, "write", lease_token=self.token),
            self.credential,
        )
        self.assertEqual(self.engine.calls, 2)

    def test_rejects_stale_claim_before_resolving(self):
        self.engine.current = False
        calls = []
        resolver = self.make_resolver(lambda context, skill: calls.append(True))
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            resolver.resolve(self.context, "write", lease_token=self.token)
        self.assertEqual(calls, [])

    def test_discards_credential_if_claim_changes_during_resolution(self):
        def consume_then_lose_claim(context, skill):
            self.engine.current = False
            return self.credential

        resolver = self.make_resolver(consume_then_lose_claim)
        with self.assertRaisesRegex(PermissionError, "changed during"):
            resolver.resolve(self.context, "write", lease_token=self.token)

    def test_rejects_context_provenance_mutation_during_lookup(self):
        def mutate_context(context, skill):
            context.task_id = "task-2"
            return self.credential

        resolver = self.make_resolver(mutate_context)
        with self.assertRaisesRegex(PermissionError, "context changed"):
            resolver.resolve(self.context, "write", lease_token=self.token)

    def test_validation_errors_fail_closed(self):
        self.engine.raise_on_validate = True
        resolver = self.make_resolver(lambda context, skill: self.credential)
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            resolver.resolve(self.context, "write", lease_token=self.token)

    def test_rejects_resolver_pinned_to_another_worker(self):
        def callback(context, skill):
            return None
        callback.worker_id = "worker-2"
        with self.assertRaisesRegex(PermissionError, "different worker"):
            self.make_resolver(callback)

    def test_rejects_invalid_claim_token_before_resolver(self):
        calls = []
        resolver = self.make_resolver(lambda context, skill: calls.append(True))
        with self.assertRaisesRegex(PermissionError, "claim token"):
            resolver.resolve(self.context, "write", lease_token="short")
        self.assertEqual(calls, [])

    def test_execution_guard_is_fail_closed_and_claim_specific(self):
        resolver = self.make_resolver(lambda context, skill: None)
        guard = resolver.execution_guard("task-1", self.token)
        self.assertTrue(guard())
        self.engine.current = False
        self.assertFalse(guard())


if __name__ == "__main__":
    unittest.main()
