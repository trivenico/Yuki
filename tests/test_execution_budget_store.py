import os
import tempfile
import threading
import unittest

from core.reasoning.execution_budget_store import (
    ExecutionBudgetStore, ExecutionBudgetStoreError,
)


class ExecutionBudgetStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "budgets.sqlite3")
        self.store = ExecutionBudgetStore(self.path)
        self.limits = {
            "max_model_turns": 2,
            "max_tool_calls": 3,
            "max_duration_ms": 1000,
            "max_transcript_chars": 5000,
        }
        self.store.create_snapshot(
            budget_id="budget-1", task_id="task-1", limits=self.limits,
            policy_version="1",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_is_durable_and_digest_verified(self):
        loaded = ExecutionBudgetStore(self.path).get_snapshot("budget-1")
        self.assertEqual(loaded.limits, self.limits)
        self.assertEqual(len(loaded.digest), 64)

    def test_snapshot_is_immutable_and_task_has_one_snapshot(self):
        with self.assertRaises(ExecutionBudgetStoreError):
            self.store.create_snapshot(
                budget_id="budget-2", task_id="task-1", limits=self.limits,
                policy_version="1",
            )

    def test_reservation_is_idempotent(self):
        first = self.store.reserve(
            budget_id="budget-1", resource="model_turns", amount=1,
            idempotency_key="turn-1",
        )
        second = self.store.reserve(
            budget_id="budget-1", resource="model_turns", amount=1,
            idempotency_key="turn-1",
        )
        self.assertEqual(first, second)
        self.assertEqual(second["model_turns"], 1)

    def test_idempotency_key_cannot_be_reused_for_different_spend(self):
        self.store.reserve(
            budget_id="budget-1", resource="tool_calls", amount=1,
            idempotency_key="tool-1",
        )
        with self.assertRaises(ExecutionBudgetStoreError):
            self.store.reserve(
                budget_id="budget-1", resource="tool_calls", amount=2,
                idempotency_key="tool-1",
            )

    def test_budget_exhaustion_rejects_additional_reservation(self):
        for index in range(2):
            self.store.reserve(
                budget_id="budget-1", resource="model_turns", amount=1,
                idempotency_key="turn-" + str(index),
            )
        with self.assertRaisesRegex(ExecutionBudgetStoreError, "budget_exhausted"):
            self.store.reserve(
                budget_id="budget-1", resource="model_turns", amount=1,
                idempotency_key="turn-overflow",
            )

    def test_parallel_reservations_cannot_overspend(self):
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def spend(key):
            barrier.wait()
            try:
                self.store.reserve(
                    budget_id="budget-1", resource="tool_calls", amount=2,
                    idempotency_key=key,
                )
                result = "reserved"
            except ExecutionBudgetStoreError:
                result = "rejected"
            with lock:
                outcomes.append(result)

        threads = [
            threading.Thread(target=spend, args=("worker-a",)),
            threading.Thread(target=spend, args=("worker-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["reserved", "rejected"])
        self.assertEqual(self.store.usage("budget-1")["tool_calls"], 2)

    def test_child_snapshot_requires_existing_parent(self):
        with self.assertRaises(ExecutionBudgetStoreError):
            self.store.create_snapshot(
                budget_id="child-1", task_id="task-child", limits=self.limits,
                policy_version="1", parent_budget_id="missing-parent",
            )


if __name__ == "__main__":
    unittest.main()
