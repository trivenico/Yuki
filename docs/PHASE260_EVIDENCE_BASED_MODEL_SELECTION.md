# Phase 260 — Evidence-based local model selection

## Delivered

- Reused the existing `core.model_capabilities` registry and task classifier rather than creating a parallel capability system.
- Extended reviewed capability records with an optional validated model digest. Existing callers remain source-compatible; local selection requires a digest match.
- Added `core/model_selection.py` with role-specific benchmark evidence, exact digest matching, conservative memory gating, score thresholds, and explicit non-activation semantics.
- Added `scripts/select_model.py` to read an operator-reviewed JSON evidence file and print recommendations for an installed local Ollama model.
- Added tests for exact digest candidate selection, digest mismatch, missing benchmark evidence, low scores, unknown hardware memory, and invalid digest.
- Documented the evidence file contract and approval boundary in README.

## Operator workflow

```bash
python scripts/models.py
python scripts/select_model.py coding --profiles ./model-evidence.json
python -m unittest tests.test_model_capabilities tests.test_local_model_registry tests.test_model_selection
```

Capability records must be reviewed and runtime-compatible. Benchmark records identify the exact installed model digest, role, score, benchmark suite/version, evaluation date, and evidence reference. The selector never downloads a model, changes the active provider, or dispatches work.

## Safety and limitations

- A benchmark score is only meaningful for its stated suite/version and evaluation conditions; it does not guarantee performance on arbitrary tasks.
- Model license status is not inferred from a model name. This phase does not perform license verification.
- Memory estimates remain heuristic and do not model context/KV cache or precise GPU residency.
- The selector is advisory. Provider activation and specialist dispatch remain separate operator-controlled actions.
- Tests are committed but have not been executed in this environment. No CI pass is claimed.
