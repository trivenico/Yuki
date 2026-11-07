# Phase 287 — Reviewer team planning and separation of duties

## Implemented

Added `automation/reviewer_team.py` with a deterministic reviewer-team planner. It maps the five required verification checks to explicit reviewer capabilities:

- `review_requirements`
- `review_testing`
- `review_security`
- `review_architecture`
- `review_provenance`

The planner selects only enabled registered specialist definitions, excludes the artifact author, and assigns a different reviewer identity to every check. It rejects missing reviewers, duplicate/unsupported checks, invalid artifact digests, and disabled candidates.

## Deliberate fail-closed behavior

The current built-in local specialists expose implementation capabilities (research, coding, testing, reporting), not the dedicated reviewer capabilities above. Therefore the planner will reject a complete reviewer-team request until separately configured reviewer specialists are registered.

This phase does not invent reviewer agents, claim that planned assignments are completed reviews, or bypass the authenticated reviewer authorizer. A plan is not an attestation. Actual reviewer execution and report submission remain separate work.

## Tests

Added tests for author exclusion, distinct reviewer identities, disabled reviewer rejection, missing reviewer rejection, and duplicate/unsupported check rejection. Tests were authored but not executed. No CI status checks were reported.
