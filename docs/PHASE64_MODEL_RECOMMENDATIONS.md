# Phase 64 — Model registry and evidence-based recommendations

Phase 64 stores sanitized evaluation metrics and ranks candidates for human review. It does not select, activate, download or deploy a model.

## Record a report

First run the Phase 63 evaluator to produce a JSON report. Then record the report:

```bash
python yuki_model_registry.py \
  --report evaluation-report.json \
  --benchmark-id starter-v1 \
  --registry .yuki/model_registry.json \
  --license-status verified \
  --runtime-status verified \
  --recommend
```

**Only mark a license or runtime as verified after you actually reviewed the official model card/license and tested the model in Yuki's intended inference runtime.** The CLI flag records the reviewer's assertion; it does not verify those claims itself. If you have not verified them, omit the flags and the candidate remains excluded from recommendations.

The local registry is a JSON file. Keep it out of version control if reports or model inventory are internal. Do not store tokens, prompts, completions, private code, or user data in the registry. Back it up if it contains important evaluation history. This first version is intended for one local process/user, not concurrent writes from multiple agents or machines.

## Recommendation policy

`--recommend` filters to the same benchmark ID, excludes candidates with restricted/unverified licenses or incompatible/unverified runtimes, and requires at least one successful case and a comparable coverage score. Remaining candidates are ranked by expected-term coverage, with latency as a tie-breaker.

Cost per 1,000 tokens is optional and shown only when the user supplies a verified estimate. The tool does not independently fetch or validate prices. Recommendations are not a global ranking: only compare models using the same versioned benchmark and comparable settings.

Every result is marked `needs_human_review` with `manual_review_required` activation policy. The tool cannot change Yuki's active provider or model.

## Current limitations

- Expected-term coverage is a narrow proxy and can reward keyword stuffing. It is not a general correctness, safety, reasoning or instruction-following score.
- Benchmark IDs are supplied by the user; keep them stable and bump the version whenever cases or scoring change.
- License/runtime statuses are reviewer assertions, not automatic checks.
- No background polling or Hugging Face notification service is installed yet. Use Phase 62 discovery to identify candidates, evaluate them explicitly, then record their reports.
- JSON file storage is local and single-writer; use a transactional database with locking/migrations before multi-agent or server deployment.

## Tests

`tests/test_model_registry.py` covers validation, invalid values, review gates, same-benchmark filtering, ranking, upsert and JSON round trips without network calls.
