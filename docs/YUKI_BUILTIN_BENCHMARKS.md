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
