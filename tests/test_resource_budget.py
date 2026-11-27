import unittest

from core.resource_budget import calculate_worker_budget


class ResourceBudgetTests(unittest.TestCase):
    def test_defaults_to_one_worker_without_guessing_memory_cost(self):
        budget = calculate_worker_budget(
            logical_cpu_count=8,
            memory_effective_available_bytes=8 * 1024**3,
        )
        self.assertEqual(budget.allowed_concurrency, 1)
        self.assertIn("single-worker default", budget.reason)

    def test_unknown_cpu_or_memory_never_increases_concurrency(self):
        for cpu, memory in ((None, 8_000_000_000), (4, None)):
            with self.subTest(cpu=cpu, memory=memory):
                budget = calculate_worker_budget(
                    logical_cpu_count=cpu,
                    memory_effective_available_bytes=memory,
                    requested_concurrency=4,
                    per_worker_memory_bytes=1_000_000_000,
                )
                self.assertEqual(budget.allowed_concurrency, 1)

    def test_parallel_budget_is_bounded_by_cpu_request_and_memory(self):
        budget = calculate_worker_budget(
            logical_cpu_count=4,
            memory_effective_available_bytes=5_000_000_000,
            requested_concurrency=8,
            per_worker_memory_bytes=1_000_000_000,
            memory_reserve_bytes=1_000_000_000,
        )
        self.assertEqual(budget.allowed_concurrency, 4)
        self.assertEqual(budget.memory_budget_bytes, 4_000_000_000)

    def test_memory_budget_can_limit_parallelism_below_cpu_count(self):
        budget = calculate_worker_budget(
            logical_cpu_count=8,
            memory_effective_available_bytes=2_500_000_000,
            requested_concurrency=8,
            per_worker_memory_bytes=1_000_000_000,
            memory_reserve_bytes=500_000_000,
        )
        self.assertEqual(budget.allowed_concurrency, 2)

    def test_insufficient_memory_falls_back_to_one_foreground_worker(self):
        budget = calculate_worker_budget(
            logical_cpu_count=8,
            memory_effective_available_bytes=500_000_000,
            requested_concurrency=4,
            per_worker_memory_bytes=1_000_000_000,
        )
        self.assertEqual(budget.allowed_concurrency, 1)
        self.assertIn("cannot safely admit parallel work", budget.reason)

    def test_rejects_invalid_budget_configuration(self):
        invalid = (
            {"requested_concurrency": 0},
            {"requested_concurrency": True},
            {"memory_reserve_bytes": -1},
            {"per_worker_memory_bytes": 0},
            {"per_worker_memory_bytes": True},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                args = {
                    "logical_cpu_count": 4,
                    "memory_effective_available_bytes": 4_000_000_000,
                }
                args.update(overrides)
                with self.assertRaises(ValueError):
                    calculate_worker_budget(**args)


if __name__ == "__main__":
    unittest.main()
