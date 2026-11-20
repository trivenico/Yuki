# Yuki built-in capability smoke suite

`evaluation.builtin_benchmarks.build_builtin_benchmark()` returns the version-1 `yuki-core-smoke` definition. Register it explicitly with `BenchmarkRegistry`; importing the module does not mutate storage or start evaluation.

## Coverage

- Reasoning: a simple arithmetic constraint.
- Coding: a small Python function contract.
- Defensive cybersecurity: offline PCAP analysis boundary; no network scan.
- Technical OSINT: unverified indicator provenance.
- Multimodal grounding: honesty when an image input is absent. This is not image understanding.
- Memory retrieval: avoid guessing when no record is supplied.
- Agent coordination: require approval before side effects.
- Reliability: do not claim an unfinished test passed.

## Interpretation and limits

These cases use required-term checks from `ModelEvaluator`. They are smoke checks only: matching words does not establish correctness, reasoning quality, security, actual multimodal perception, memory retrieval quality, or agent execution safety. Use task-specific human-reviewed datasets and the deterministic output-hash evaluator for stronger reproducibility. The suite does not call providers, dispatch agents, or authorize promotion.

## End-to-end evaluation

The suite can be registered and run through `EvaluationService` with a server-constructed `TrustedEvaluationAdapter`. Pin all three revision fields (`benchmark_id`, `benchmark_version`, and `benchmark_digest`) in the run request. The service records results in the owner-scoped `EvaluationRunStore`; reuse an idempotency key only for the same owner and exact request. The adapter's `generate_with_timeout` implementation must enforce the supplied timeout in its underlying transport—Python cannot preempt a blocking synchronous call.

```python
from evaluation import (
    BenchmarkRegistry, EvaluationRunStore, EvaluationService,
    TrustedEvaluationAdapter, build_builtin_benchmark,
)

registry = BenchmarkRegistry(database_path="state/benchmarks.sqlite3")
suite = registry.register(build_builtin_benchmark())
store = EvaluationRunStore("state/evaluation-runs.sqlite3")
adapter = TrustedEvaluationAdapter(
    "configured-provider", "configured-model", provider_generate_with_timeout,
)
service = EvaluationService(registry, store, resolve_owner_candidate_adapter)
run = service.run(
    owner_id=owner_id, idempotency_key=request_id,
    benchmark_id=suite.benchmark_id, benchmark_version=suite.version,
    benchmark_digest=suite.digest, candidate_id=candidate_id,
)
```

Do not expose arbitrary provider callables or owner IDs directly to untrusted clients. Resolve owner identity from the authenticated local owner context. Store files require private filesystem permissions and backup/retention policy. A successful smoke-suite run is not a production promotion decision.
