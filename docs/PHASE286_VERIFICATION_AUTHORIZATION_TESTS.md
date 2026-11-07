# Phase 286 — Verification authorization contract regression fixes

## Changes

The verification evidence store has a fail-closed authorization contract: report authors require `verification:submit`, and every check reviewer requires `verification:review`, both scoped to the exact artifact digest.

The existing evidence-store tests had drifted from that contract. Two tests attempted to persist reports without configuring the required trusted authorizer, so they would fail before exercising their intended assertions. Updated those tests to use the explicit test-only authorizer.

Added a focused test proving that:
- The report author is checked for `verification:submit`.
- Every reviewer is checked for `verification:review`.
- Every authorization decision receives the report's exact artifact SHA-256.

## Reviewer orchestration status

This phase does not claim that dedicated reviewer agents are operational. The local built-in specialist definitions do not yet expose authenticated reviewer principals and separate reviewer execution handlers. The coordinator adapter still requires a trusted principal resolver. No permissive local identity mapping has been introduced.

## Validation

Changes were fetched back from GitHub after commit. Tests were authored but not executed in this environment. CI status checks were empty; this is not evidence that tests pass.
