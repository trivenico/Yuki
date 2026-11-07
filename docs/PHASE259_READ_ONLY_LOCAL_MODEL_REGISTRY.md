# Phase 259 — Read-only local model registry

## Delivered

- Added `core/local_model_registry.py` to discover installed Ollama models through the loopback `/api/tags` endpoint.
- Inventory records model name, digest, size, modified time, family, parameter size, and quantization metadata when provided.
- Added bounded response size and timeout, strict loopback-only URL validation, and read-only GET semantics.
- Added conservative memory estimates using parameter count and quantization hints. Estimates include a small fixed overhead and are explicitly heuristics, not fit guarantees.
- License status defaults to `unknown`; task-role suitability defaults to `unverified`. The registry does not infer licenses or quality from model names.
- Added `scripts/models.py` for human-readable and JSON inventory output.
- Added tests for inventory parsing, loopback enforcement, invalid payloads, response bounds, and recommendation behavior.

## Run

```bash
python scripts/models.py
python scripts/models.py --json
python -m unittest tests.test_local_model_registry
```

Ollama must be running locally for discovery. The command does not download, delete, load, or execute any model.

## Limitations

- No model download manager or integrity-verification workflow is implemented in this phase.
- License provenance requires authoritative source metadata and remains unknown by default.
- Role quality has not been benchmarked; every role fit remains unverified.
- Memory estimates do not accurately model context size, KV cache, runtime overhead, GPU offload, or concurrent model residency.
- Tests have been committed but not executed in this environment. No CI pass is claimed.
