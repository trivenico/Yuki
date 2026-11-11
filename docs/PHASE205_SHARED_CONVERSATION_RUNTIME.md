# Phase 205 — shared conversation application runtime

**Status:** Integrated refactor under CI; not a production-readiness claim.

## Objective

Use one composition and lifecycle boundary for the durable conversation service
across the authenticated API and installed CLI. This prevents persistence and
conversation-service construction from drifting as companion and desktop
surfaces are added.

## Contract

- `build_conversation_application_runtime` owns construction of the configured
  conversation store and `ConversationService`.
- The API composes this shared runtime but continues to derive owner identity
  exclusively from its authenticated session. The shared runtime receives no
  API owner ID and does not authenticate network callers.
- The CLI supplies its stable local owner-scope ID. That identifier is not a
  network credential and grants no remote access.
- Tool authorization remains surface-specific. The CLI continues to use an
  empty guarded registry with deny-all authorization.
- Runtime shutdown is idempotent and closes the owned store once.
- Provider construction, API authentication, approvals, memory, task workers,
  and device capabilities are not silently enabled by this refactor.

## Validation

Focused tests cover shared construction, owner-scope validation, dependency
validation, and idempotent resource shutdown. API and CLI integration suites
remain required on the exact pull-request head. No local test execution or
production deployment is claimed.
