# Phase 62 — Hugging Face model discovery

Yuki's discovery feature searches Hugging Face Hub model metadata through the read-only `HuggingFaceDiscovery` adapter. It does not download model weights, install dependencies, execute repository code, or change the active provider.

## Canonical technical specification

See [PHASE62_HUGGINGFACE_MODEL_DISCOVERY.md](PHASE62_HUGGINGFACE_MODEL_DISCOVERY.md) for the complete security contract, supported tasks and sort orders, request/response bounds, token handling, output schema, review requirements, and regression-test coverage.

## CLI

Run:

```bash
python yuki_model_discovery.py --task text-generation --sort downloads --limit 20
```

Optional flags include `--timeout` (1–30 seconds) and `--output PATH` (creates a new report and refuses to overwrite an existing file). An optional `HF_TOKEN` environment variable is sent only in the Authorization header to the fixed Hugging Face Hub API endpoint.

## Trust policy

Discovery results are metadata, not endorsements or benchmark scores. Every candidate requires human review of its official model card, license, provenance, security posture, runtime compatibility, hardware requirements, and task-specific evaluation before use. Missing license metadata is a review blocker. The tool never auto-enables or auto-downloads models.

The implementation is in `providers/huggingface_discovery.py`; its mocked regression suite is `tests/test_huggingface_discovery.py`.
