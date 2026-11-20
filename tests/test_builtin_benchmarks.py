"""Tests for the built-in benchmark smoke suite."""
import unittest

from evaluation.benchmark_registry import BenchmarkRegistry
from evaluation.builtin_benchmarks import (
    BUILTIN_BENCHMARK_ID, BUILTIN_BENCHMARK_VERSION, build_builtin_benchmark,
)

class BuiltinBenchmarkTests(unittest.TestCase):
    def test_suite_has_expected_bounded_capability_coverage(self):
        suite = build_builtin_benchmark()
        self.assertEqual(suite.benchmark_id, BUILTIN_BENCHMARK_ID)
        self.assertEqual(suite.version, BUILTIN_BENCHMARK_VERSION)
        self.assertEqual(len(suite.cases), 8)
        self.assertEqual(len({case.case_id for case in suite.cases}), 8)
        self.assertEqual({case.task for case in suite.cases}, {
            "reasoning", "coding", "defensive_cybersecurity", "technical_osint",
            "multimodal_grounding", "memory_retrieval", "agent_coordination", "reliability",
        })
        self.assertEqual(len(suite.digest), 64)

    def test_suite_is_compatible_with_immutable_registry(self):
        registry = BenchmarkRegistry()
        first = registry.register(build_builtin_benchmark())
        second = registry.register(build_builtin_benchmark())
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(registry.resolve(BUILTIN_BENCHMARK_ID, 1, expected_digest=first.digest), first)
        with self.assertRaises(ValueError):
            registry.register(type(first)(first.benchmark_id, first.version, first.name,
                                          first.cases[:-1], first.description))

if __name__ == "__main__":
    unittest.main()
