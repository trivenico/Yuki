# Phase 284 — Coordinator-backed reviewer authorization adapter

## Implemented

Added `coordinator_reviewer_authorization.py`, an adapter for the durable verification evidence store. It checks:

- Reviewer is present in the current specialist registry and enabled.
- Reviewer has the exact `verification:submit` or `verification:review` permission.
- A trusted identity resolver maps the registered agent ID to an authenticated principal.
- The coordinator's current authorization policy grants that principal the requested action against the exact artifact digest.
- Resolver or policy errors fail closed.

The adapter refuses construction without an explicit trusted principal resolver. It never treats an agent ID from report JSON as proof of authentication.

## Why startup wiring is deferred

The current terminal worker runtime does not expose a trusted agent-ID-to-authenticated-principal resolver for verification reviewers. Wiring a permissive resolver or using the agent ID as its own principal would defeat the identity boundary. Therefore the adapter is implemented and unit-tested, but it is not yet activated in `main.py`. The verification evidence store continues to fail closed unless a trusted authorizer is supplied.

## Tests

Added tests for registry membership, enabled status, explicit permissions, authenticated principal resolution, coordinator policy decisions, unknown actions, and policy failures. Tests were authored but not executed. No production identity provider or full reviewer orchestration is claimed.
