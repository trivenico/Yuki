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

    def test_child_snapshot_cannot_bypass_allocation_api(self):
        with self.assertRaisesRegex(ExecutionBudgetStoreError, "allocate_child_snapshot"):
            self.store.create_snapshot(
                budget_id="child-1", task_id="task-child", limits=self.limits,
                policy_version="1", parent_budget_id="budget-1",
            )

    def test_child_allocations_reserve_parent_capacity(self):
        child_limits = {
            "max_model_turns": 1, "max_tool_calls": 1,
            "max_duration_ms": 400, "max_transcript_chars": 2000,
        }
        child = self.store.allocate_child_snapshot(
            parent_budget_id="budget-1", child_budget_id="child-1",
            task_id="child-task-1", limits=child_limits, policy_version="1",
            allocation_key="research-team",
        )
        self.assertEqual(child.parent_budget_id, "budget-1")
        second = self.store.allocate_child_snapshot(
            parent_budget_id="budget-1", child_budget_id="child-2",
            task_id="child-task-2", limits=child_limits, policy_version="1",
            allocation_key="engineering-team",
        )
        self.assertEqual(second.parent_budget_id, "budget-1")
        with self.assertRaisesRegex(ExecutionBudgetStoreError, "parent_budget_exhausted"):
            self.store.allocate_child_snapshot(
                parent_budget_id="budget-1", child_budget_id="child-3",
                task_id="child-task-3", limits=child_limits, policy_version="1",
                allocation_key="overflow-team",
            )

    def test_child_allocation_is_idempotent_but_not_mutable(self):
        limits = {
            "max_model_turns": 1, "max_tool_calls": 1,
            "max_duration_ms": 400, "max_transcript_chars": 2000,
        }
        first = self.store.allocate_child_snapshot(
            parent_budget_id="budget-1", child_budget_id="child-1",
            task_id="child-task-1", limits=limits, policy_version="1",
            allocation_key="research",
        )
        second = self.store.allocate_child_snapshot(
            parent_budget_id="budget-1", child_budget_id="child-1",
            task_id="child-task-1", limits=limits, policy_version="1",
            allocation_key="research",
        )
        self.assertEqual(first.digest, second.digest)
        changed = dict(limits, max_tool_calls=2)
        with self.assertRaisesRegex(ExecutionBudgetStoreError, "allocation_key_conflict"):
            self.store.allocate_child_snapshot(
                parent_budget_id="budget-1", child_budget_id="child-1",
                task_id="child-task-1", limits=changed, policy_version="1",
                allocation_key="research",
            )

    def test_parallel_child_allocations_cannot_overallocate_parent(self):
        child_limits = {
            "max_model_turns": 1, "max_tool_calls": 2,
            "max_duration_ms": 500, "max_transcript_chars": 2500,
        }
        barrier = threading.Barrier(2)
        outcomes = []
        lock = threading.Lock()

        def allocate(key):
            barrier.wait()
            try:
                self.store.allocate_child_snapshot(
                    parent_budget_id="budget-1", child_budget_id="child-" + key,
                    task_id="task-" + key, limits=child_limits, policy_version="1",
                    allocation_key=key,
                )
                result = "allocated"
            except ExecutionBudgetStoreError:
                result = "rejected"
            with lock:
                outcomes.append(result)

        workers = [
            threading.Thread(target=allocate, args=("team-a",)),
            threading.Thread(target=allocate, args=("team-b",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertCountEqual(outcomes, ["allocated", "rejected"])


if __name__ == "__main__":
    unittest.main()
