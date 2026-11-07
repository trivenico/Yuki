# Phase 283 — Trusted reviewer authorization

## Change

`VerificationEvidenceStore` now requires an explicit trusted `reviewer_authorizer` callback before it accepts a report. The callback is invoked separately for:

- The report author with action `verification:submit`.
- Every check reviewer with action `verification:review`.

Both calls are bound to the report's exact artifact SHA-256. Missing authorization configuration fails closed; a callback must return the literal boolean `True`. Unauthorized report authors or reviewers are rejected before persistence.

The existing report validation still rejects author self-review, duplicate checks, unsupported checks, and incomplete required evidence. The existing sandbox execution-evidence validator remains an additional check for test/security execution references.

## Integration requirement

The callback must be backed by Yuki's authenticated coordinator/agent registry and action policy. It must not be a permissive lambda or trust agent IDs supplied by report content. This phase deliberately does not invent a runtime identity provider or silently enable report submission in startup.

## Tests

- Existing store tests now provide an explicit test-only authorizer.
- Added coverage for missing authorizer configuration.
- Added coverage for a reviewer identity denied by the authorizer.

Tests have been authored but not executed. No claim of production reviewer authentication is made until the real coordinator-backed authorizer is wired and exercised end-to-end.
