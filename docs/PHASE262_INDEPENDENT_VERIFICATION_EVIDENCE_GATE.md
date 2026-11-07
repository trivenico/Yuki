# Phase 262 — Independent Verification Evidence Gate

## Delivered

Added `automation/independent_verification.py`, a deterministic, fail-closed
evidence evaluator for artifacts produced by specialist agents.

The default acceptance policy requires five independent checks:

- Acceptance criteria
- Tests
- Security
- Architecture
- Claims and provenance

Every check must identify a reviewer agent different from the artifact author,
a status, an evidence reference, and a bounded summary. The report binds to
the exact artifact bytes through a SHA-256 digest and requires an aware UTC
timestamp.

The evaluator rejects missing, failed, blocked, or not-run required checks.
Duplicate checks, invalid reviewer identities, and self-review are rejected.

Added `tests/test_independent_verification.py` covering acceptance, missing
evidence, non-passing states, self-review, duplicate checks, timestamps, and
artifact digest binding.

## Explicit limitations

This is an evidence gate, not an execution or security-scanning system. It does
not run tests, inspect source code, perform threat modeling, or prove that an
evidence reference is truthful. A trusted orchestrator must obtain evidence
from actual test runners, scanners, and reviewers before constructing a report.
The module is not yet wired into coordinator task completion or dashboard UI.

The evaluator currently uses reviewer agent IDs as independence identities.
Production integration should additionally verify reviewer registration,
authorization, role separation, and immutable evidence provenance.

Tests are authored but have not been executed in this environment. No CI result
or production readiness is claimed.

## Next

Integrate verification tasks and immutable artifact references into coordinator
completion. Ensure an implementation task cannot be marked accepted solely by
its author or by a self-reported success string.
