# Phase 298 — Coordinator-controlled reviewer provisioning

## Implemented

Adds `ReviewerOrchestrator` as a host-side orchestration boundary. It:

1. Authorizes the coordinator for `review:orchestrate` and the exact artifact digest.
2. Builds a deterministic reviewer team with separation of duties.
3. Validates bounded ReviewTask contracts and required check inputs.
4. Issues short-lived sessions to the assigned reviewer identities.
5. Persists the tasks through the authorization-gated reviewer ledger.
6. Returns task IDs and each reviewer's scoped bearer token to the trusted host caller.

The orchestrator does not execute worker code, expose signing keys, submit results,
or claim that review is complete. Tokens must be delivered only by a trusted
host transport and must never be written to prompts or logs.

## Failure behavior

Authorization, planning, task-contract, session-provisioning, or persistence
errors fail closed. Sessions are minted before task persistence so they are
never returned when task creation fails; unused tokens remain bounded by their
short TTL. The ledger remains the durable source of task state.

## Runtime integration boundary

This phase adds a reusable orchestration service and tests. The default CLI,
local worker runtime, and coordinator dispatch loop are not yet wired to it.
A future integration must supply authenticated coordinator policy, a protected
session secret, host-managed reviewer keys, and a trusted worker transport.
Reviewers still need a controlled execution adapter before autonomous review
can be enabled.

## Tests

Three tests cover successful provisioning, coordinator denial, and session
policy failure. Tests are authored but not executed in this environment.
