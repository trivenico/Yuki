# Phase 291 — Reviewer-to-verification bridge

## Scope

Adds an explicit bridge from submitted SQLite reviewer-ledger results to
VerificationEvidenceStore. The bridge does not dispatch agents, execute checks,
authenticate referenced source evidence, or grant acceptance by itself.

## Safety rules

- Every task must be in the submitted state.
- Task, result, author, reviewer, check name, and artifact SHA-256 bindings are
  checked before constructing a VerificationReport.
- Duplicate task IDs and duplicate check names are rejected.
- If callers specify required checks, the supplied submitted reviews must match
  that set exactly.
- The evidence store continues to enforce reviewer authorization and any
  configured execution-receipt validator.
- A partial set of reviews may be recorded as an explicitly incomplete report
  only when no exact required set is supplied; the verification evaluator
  remains fail-closed for its required checks.

## Evidence references

The bridge emits `yuki-review-result://<task_id>/<result_digest>` references.
These identify immutable ledger result records; they are not proof that the
underlying evidence references inside a review were independently verified.
That trust boundary remains explicit.

## Tests

Adds regression coverage for successful result conversion, unsubmitted tasks,
artifact mismatch, and author mismatch. Also repairs the missing `asdict`
import in the reviewer ledger, required for result serialization.

Tests are authored but must be run in the repository's supported Python
environment before treating this phase as runtime-verified.
