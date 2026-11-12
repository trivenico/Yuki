import tempfile
import time
import threading
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

    def test_adapter_receives_remaining_suite_deadline(self):
        registry = BenchmarkRegistry()
        benchmark = registry.register(BenchmarkDefinition(
            benchmark_id="two-case-deadline",
            version=1,
            name="Two case deadline",
            cases=(
                EvalCase("case-1", "reasoning", "First case", max_latency_seconds=3),
                EvalCase("case-2", "reasoning", "Second case", max_latency_seconds=3),
            ),
        ))
        timeouts = []

        def generate(prompt, *, timeout_seconds):
            timeouts.append(timeout_seconds)
            if prompt == "First case":
                time.sleep(0.15)
            return "A retry is bounded."

        adapter = TrustedEvaluationAdapter("provider-a", "model-a", generate)
        service = EvaluationService(
            registry, self.store, lambda owner, candidate: adapter,
            evaluator=ModelEvaluator(max_total_duration_seconds=1),
        )
        service.run(
            owner_id="owner-a", idempotency_key="remaining-budget",
            benchmark_id=benchmark.benchmark_id, benchmark_version=benchmark.version,
            benchmark_digest=benchmark.digest, candidate_id="candidate-a",
        )
        self.assertEqual(len(timeouts), 2)
        self.assertGreater(timeouts[0], timeouts[1])
        self.assertLess(timeouts[1], 0.9)
        self.assertGreater(timeouts[1], 0.5)

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

    def test_separate_service_instances_cannot_execute_same_live_request(self):
        from evaluation.run_store import EvaluationRunInProgress
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def generate(prompt, *, timeout_seconds):
            calls.append(prompt)
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test execution release timed out")
            return "A bounded retry is safe."

        adapter = TrustedEvaluationAdapter("provider-a", "model-a", generate)
        first = self._service(adapter)
        second = self._service(adapter)
        result_holder = []
        error_holder = []

        def execute_first():
            try:
                result_holder.append(first.run(
                    owner_id="owner-a", idempotency_key="cross-worker-key",
                    benchmark_id="smoke", benchmark_version=1,
                    benchmark_digest=self.benchmark.digest, candidate_id="candidate-a",
                ))
            except Exception as exc:
                error_holder.append(exc)

        worker = threading.Thread(target=execute_first)
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=3))
            with self.assertRaises(EvaluationRunInProgress):
                second.run(
                    owner_id="owner-a", idempotency_key="cross-worker-key",
                    benchmark_id="smoke", benchmark_version=1,
                    benchmark_digest=self.benchmark.digest, candidate_id="candidate-a",
                )
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(error_holder, [])
        self.assertEqual(len(result_holder), 1)
        self.assertEqual(calls, ["Explain bounded retries"])

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
