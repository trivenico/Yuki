"""Trusted execution orchestration for registered model benchmarks.

Only server-configured adapters execute cases. Callers select an owner-scoped
candidate; they cannot submit an EvalReport or a provider callable.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable, Mapping

from evaluation.benchmark_registry import BenchmarkRegistry
from evaluation.model_eval import EvalReport, ModelEvaluator
from evaluation.run_store import EvaluationRun, EvaluationRunConflict, EvaluationRunStore


@dataclass(frozen=True, slots=True)
class TrustedEvaluationAdapter:
    """A server-constructed provider adapter with a real per-call timeout.

    generate_with_timeout must enforce the supplied deadline in the underlying
    transport/process. The evaluator cannot preempt a synchronous Python call.
    """
    provider_id: str
    model_id: str
    generate_with_timeout: Callable[..., str]

    def __post_init__(self) -> None:
        for value, field, limit in (
            (self.provider_id, "provider_id", 100),
            (self.model_id, "model_id", 200),
        ):
            if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
                raise ValueError(f"{field} must be non-empty bounded text")
        if not callable(self.generate_with_timeout):
            raise TypeError("generate_with_timeout must be callable")


class EvaluationService:
    """Run a pinned benchmark using an owner-authorized, trusted model adapter."""

    def __init__(
        self,
        registry: BenchmarkRegistry,
        store: EvaluationRunStore,
        adapter_for_candidate: Callable[[str, str], TrustedEvaluationAdapter],
        *,
        evaluator: ModelEvaluator | None = None,
        environment: Mapping[str, str] | None = None,
        max_concurrent_runs: int = 2,
    ) -> None:
        if not isinstance(registry, BenchmarkRegistry):
            raise TypeError("registry must be a BenchmarkRegistry")
        if not isinstance(store, EvaluationRunStore):
            raise TypeError("store must be an EvaluationRunStore")
        if not callable(adapter_for_candidate):
            raise TypeError("adapter_for_candidate must be callable")
        if evaluator is not None and not isinstance(evaluator, ModelEvaluator):
            raise TypeError("evaluator must be a ModelEvaluator")
        if type(max_concurrent_runs) is not int or not 1 <= max_concurrent_runs <= 32:
            raise ValueError("max_concurrent_runs must be between 1 and 32")
        self.registry = registry
        self.store = store
        self.adapter_for_candidate = adapter_for_candidate
        self.evaluator = evaluator or ModelEvaluator()
        self.environment = dict(environment or {})
        self._slots = threading.BoundedSemaphore(max_concurrent_runs)

    def run(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        benchmark_id: str,
        benchmark_version: int,
        benchmark_digest: str,
        candidate_id: str,
    ) -> EvaluationRun:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 200:
            raise ValueError("owner_id must be non-empty bounded text")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must be non-empty bounded text")
        if not isinstance(candidate_id, str) or not candidate_id.strip() or len(candidate_id) > 200:
            raise ValueError("candidate_id must be non-empty bounded text")
        if not self._slots.acquire(blocking=False):
            raise EvaluationCapacityError("evaluation capacity is currently full")
        try:
            benchmark = self.registry.resolve(
                benchmark_id, benchmark_version, expected_digest=benchmark_digest,
            )
            adapter = self.adapter_for_candidate(owner_id, candidate_id)
            if not isinstance(adapter, TrustedEvaluationAdapter):
                raise TypeError("candidate resolver must return a TrustedEvaluationAdapter")
            previous = self.store.get_by_idempotency(
                owner_id=owner_id, idempotency_key=idempotency_key,
            )
            if previous is not None:
                same_request = (
                    previous.benchmark_id == benchmark.benchmark_id
                    and previous.benchmark_version == benchmark.version
                    and previous.benchmark_digest == benchmark.digest
                    and previous.candidate_id == candidate_id
                    and previous.provider_id == adapter.provider_id
                    and previous.model_id == adapter.model_id
                    and previous.environment == self.environment
                )
                if not same_request:
                    raise EvaluationRunConflict(
                        "idempotency key was used for a different evaluation request"
                    )
                return previous
            request_digest = self.store.request_digest_for(
                benchmark_id=benchmark.benchmark_id,
                benchmark_version=benchmark.version,
                benchmark_digest=benchmark.digest,
                candidate_id=candidate_id,
                provider_id=adapter.provider_id,
                model_id=adapter.model_id,
                environment=self.environment,
            )
            lease_token = self.store.acquire_claim(
                owner_id=owner_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if lease_token is None:
                completed = self.store.get_by_idempotency(
                    owner_id=owner_id, idempotency_key=idempotency_key,
                )
                if completed is None:
                    raise RuntimeError("completed evaluation disappeared after claim acquisition")
                return completed
            try:
                case_index = 0

                def generate(prompt: str) -> str:
                    nonlocal case_index
                    case = benchmark.cases[case_index]
                    case_index += 1
                    # The adapter owns the hard timeout; ModelEvaluator measures and
                    # reports latency but cannot safely kill a blocked Python call.
                    return adapter.generate_with_timeout(
                        prompt,
                        timeout_seconds=min(
                            float(case.max_latency_seconds),
                            self.evaluator.max_total_duration_seconds,
                        ),
                    )

                report = self.evaluator.run(
                    candidate_id, generate, benchmark.cases, approval_required=True,
                )
                if not isinstance(report, EvalReport):
                    raise RuntimeError("evaluator returned an invalid report")
                return self.store.record(
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    benchmark_id=benchmark.benchmark_id,
                    benchmark_version=benchmark.version,
                    benchmark_digest=benchmark.digest,
                    candidate_id=candidate_id,
                    provider_id=adapter.provider_id,
                    model_id=adapter.model_id,
                    environment=self.environment,
                    report=report,
                    lease_token=lease_token,
                )
            finally:
                self.store.release_claim(
                    owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    lease_token=lease_token,
                )
        finally:
            self._slots.release()


class EvaluationCapacityError(RuntimeError):
    """The configured number of concurrent benchmark runs is already active."""
