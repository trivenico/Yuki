# Yuki benchmark evaluation

Yuki's benchmark evaluator is a deterministic scoring boundary for outputs that have already been produced. It does not call providers, execute tools, train models, change routing, or promote releases.

## Current contract

- Input is a bounded sequence of benchmark observations with stable case IDs, categories, expected output, actual output, latency, cost, and an explicitly supplied safety-violation flag.
- Correctness currently means exact UTF-8 output equality. This is intentionally conservative and is not a semantic-quality metric.
- The report stores SHA-256 digests of expected and actual outputs, not the raw text or prompts.
- Aggregates include exact-match rate, mean latency, nearest-rank p95 latency, total cost, and safety-violation count/rate.
- Reports are canonical-digest bound and can be integrity checked with `validate_benchmark_report`.
- Input provenance is owner-supplied and unverified. The evaluator cannot independently verify timing, cost, model identity, safety labels, or that outputs came from the declared model.

## Limitations and next gates

No benchmark corpus, human grading, statistical confidence intervals, semantic judge, cross-model comparison, trend store, or release gate is implemented yet. Safety-violation labels are supplied by the benchmark harness and must not be interpreted as an automated security assessment. Do not promote models based on this report alone. Future comparisons must use the same immutable held-out cases, evaluator version, and scoring rubric, and require human review.
