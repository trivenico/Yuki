# Phase 175 — Adaptive local model admission

## Delivered

`providers/adaptive_model_policy.py` now connects the compact/standard/
expanded device execution profile to Yuki's existing Ollama installed-model
inventory and conservative resource-fit estimator.

For each installed model it:
- Calculates a conservative host-memory requirement from artifact size.
- Compares the estimate plus headroom against the device's model/runtime
  budget, not all available system RAM.
- Treats GPU memory as optional acceleration; CPU-only remains valid.
- Separates eligible-for-benchmark models from excluded candidates.
- Explicitly avoids downloads, model loading and activation.

## Safety and compatibility

This is an advisory resource gate, not a complete model-readiness decision.
Existing digest, ownership, license, evaluation, provenance and human approval
gates remain mandatory. An eligible model must still pass a measured runtime
smoke test and peak-memory benchmark before activation.

The 8 GiB tier deliberately budgets at most 35% of physical RAM and 50% of
currently available RAM. This may exclude some models that happen to run on a
particular machine; real-device benchmarks should calibrate the heuristics
before relaxing the policy.
