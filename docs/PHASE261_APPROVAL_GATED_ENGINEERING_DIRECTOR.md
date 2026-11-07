# Phase 261 — Approval-Gated Engineering Director

## Scope

Added `automation/engineering_director.py` as a deterministic proposal layer over
the existing specialist coordinator and evidence-based model selector.

## Behavior

- Classifies a bounded operator goal using the existing explainable task classifier.
- Creates proposal tasks with explicit acceptance criteria.
- Requires an enabled registered specialist for each required capability.
- Includes model candidates only when a reviewed capability registry and installed
  model inventory are supplied; selection remains recommendation-only.
- Leaves independent capability tasks dependency-free so the coordinator may
  dispatch them concurrently subject to its existing policy and worker limits.
- Requires an explicit `approved=True` argument before submitting a plan.
- Submits through the existing coordinator, preserving its authorization,
  idempotency, approval and budget-allocation controls.

## Safety boundaries

This phase does not invoke an LLM, activate a model, download models, dispatch
tasks, execute generated code, or modify files. The proposal is deterministic
and should be treated as a first orchestration layer, not autonomous engineering.

Model capability labels and coordinator specialist capabilities are distinct
registries. This adapter only recommends model candidates for the classifier's
required capability labels; it does not infer a mapping between different labels.

## Tests

Added `tests/test_engineering_director.py` for proposal-only behavior, missing
specialist rejection, explicit approval enforcement, and coordinator submission.

Tests have been authored but not executed in this environment. No CI result is
claimed.

## Remaining work

- Add a reviewed mapping between engineering roles and model capabilities.
- Add real dependency-aware planning rather than inferring dependencies from
  capability classification.
- Wire proposal/review/submit into the CLI and dashboard.
- Add independent verification tasks and structured artifact/result contracts.
- Run the full test suite and platform smoke tests before calling this usable.
