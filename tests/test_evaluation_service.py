import tempfile
import unittest
from pathlib import Path

from evaluation.benchmark_registry import BenchmarkDefinition, BenchmarkRegistry
from evaluation.model_eval import EvalCase, ModelEvaluator
from evaluation.run_store import EvaluationRunStore
from evaluation.service import (
    EvaluationCapacityError,
    EvaluationService,
    TrustedEvaluationAdapter,
)


class EvaluationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = EvaluationRunStore(Path(self.temp_dir.name) / "runs.sqlite3")
        self.registry = BenchmarkRegistry()
        self.benchmark = self.registry.register(BenchmarkDefinition(
            benchmark_id="smoke",
            version=1,
            name="Smoke benchmark",
            cases=(EvalCase(
                case_id="case-1",
                task="reasoning",
                prompt="Explain bounded retries",
                required_terms=("retry",),
                max_latency_seconds=3,
            ),),
        ))
        self.resolutions = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def _service(self, adapter, **kwargs):
        def resolve(owner_id, candidate_id):
            self.resolutions.append((owner_id, candidate_id))
            return adapter
        return EvaluationService(
            self.registry, self.store, resolve,
            evaluator=ModelEvaluator(max_total_duration_seconds=10),
            **kwargs,
        )

    def test_executes_registered_cases_through_server_adapter_and_persists(self):
        calls = []
        adapter = TrustedEvaluationAdapter(
            "provider-a", "model-a",
            lambda prompt, *, timeout_seconds: (
                calls.append((prompt, timeout_seconds)) or "A bounded retry is safe."
            ),
        )
        result = self._service(adapter).run(
            owner_id="owner-a",
            idempotency_key="request-1",
            benchmark_id="smoke",
            benchmark_version=1,
            benchmark_digest=self.benchmark.digest,
            candidate_id="candidate-a",
        )
        self.assertEqual(self.resolutions, [("owner-a", "candidate-a")])
        self.assertEqual(calls, [("Explain bounded retries", 3.0)])
        self.assertEqual(result.provider_id, "provider-a")
        self.assertEqual(result.model_id, "model-a")
        self.assertEqual(result.report["passed_count"], 1)
        self.assertEqual(self.store.get(owner_id="owner-a", run_id=result.run_id), result)
        replay = self._service(adapter).run(
            owner_id="owner-a",
            idempotency_key="request-1",
            benchmark_id="smoke",
            benchmark_version=1,
            benchmark_digest=self.benchmark.digest,
            candidate_id="candidate-a",
        )
        self.assertEqual(replay.run_id, result.run_id)
        self.assertEqual(len(calls), 1)

    def test_digest_mismatch_fails_before_provider_resolution(self):
        adapter = TrustedEvaluationAdapter("provider-a", "model-a", lambda *a, **k: "retry")
        with self.assertRaisesRegex(ValueError, "digest"):
            self._service(adapter).run(
                owner_id="owner-a", idempotency_key="request-1",
                benchmark_id="smoke", benchmark_version=1,
                benchmark_digest="0" * 64, candidate_id="candidate-a",
            )
        self.assertEqual(self.resolutions, [])

    def test_adapter_timeout_is_recorded_as_failed_case(self):
        def timeout(prompt, *, timeout_seconds):
            raise TimeoutError("provider deadline")
        adapter = TrustedEvaluationAdapter("provider-a", "model-a", timeout)
        result = self._service(adapter).run(
            owner_id="owner-a", idempotency_key="request-2",
            benchmark_id="smoke", benchmark_version=1,
            benchmark_digest=self.benchmark.digest, candidate_id="candidate-a",
        )
        self.assertEqual(result.report["passed_count"], 0)
        self.assertEqual(result.report["results"][0]["error_category"], "timeout")

    def test_rejects_untrusted_adapter_resolver_result(self):
        service = self._service(object())
        with self.assertRaisesRegex(TypeError, "TrustedEvaluationAdapter"):
            service.run(
                owner_id="owner-a", idempotency_key="request-3",
                benchmark_id="smoke", benchmark_version=1,
                benchmark_digest=self.benchmark.digest, candidate_id="candidate-a",
            )

    def test_capacity_is_bounded(self):
        service = self._service(
            TrustedEvaluationAdapter("provider-a", "model-a", lambda *a, **k: "retry"),
            max_concurrent_runs=1,
        )
        self.assertTrue(service._slots.acquire(blocking=False))
        try:
            with self.assertRaises(EvaluationCapacityError):
                service.run(
                    owner_id="owner-a", idempotency_key="request-4",
                    benchmark_id="smoke", benchmark_version=1,
                    benchmark_digest=self.benchmark.digest, candidate_id="candidate-a",
                )
        finally:
            service._slots.release()


if __name__ == "__main__":
    unittest.main()
