# Phase 228 — Owner-isolated provider execution

## Scope

Adds an explicit authenticated-owner execution-loop factory to the conversation
service, shared application runtime and ASGI runtime. The authenticated API
passes the owner identity only after the conversation service has verified
conversation ownership, request idempotency and optimistic version state.

## Provider isolation

`OwnerAgentLoopFactory` constructs a fresh provider manager, model adapter and
agent execution loop for each turn. It loads only the authenticated owner's
persisted provider profiles, resolves the selected credential through the OS
vault, and uses an empty environment mapping so process-level API-key variables
cannot become a fallback. A missing, unselected or unavailable profile fails
closed. No mutable provider adapter is cached across owners.

## Tool isolation

The host may supply `tool_runtime_for_owner(owner_id)`. It must create a fresh
`GuardedToolRuntime` whose handlers, authorization policy, approvals and audit
callbacks are bound to that authenticated owner. Runtime and policy objects are
tracked by weak identity and cannot be shared across owners. Without a callback,
the factory supplies an empty deny-all runtime. Provider credentials never
grant tool permissions.

The host must ensure the tool schemas returned by `tools_for_user` correspond
to the handlers and policy installed in that owner's guarded runtime. The
factory does not infer or silently broaden tool permissions.

## Conversation concurrency

Replaces the process-wide in-memory conversation lock with reference-counted
per-owner/per-conversation locks. Turns in one conversation remain serialized;
independent conversations can execute concurrently. Durable generation leases,
optimistic versions, idempotency and interrupted-generation reconciliation
remain in place for coordination across service instances.

## Integration contract

Pass an `OwnerAgentLoopFactory` instance as `agent_loop_for_owner` to
`build_conversation_runtime`. Supply an owner-bound tool-runtime callback only
after its handler, approval and audit ownership has been tested. The provider
profile API remains a separate lifecycle surface; merely enabling profile
management does not implicitly enable provider switching. The deployment must
explicitly compose the profile store and owner loop factory.

## Remaining work

- End-to-end authenticated API test: create two accounts, configure distinct
  profiles and vault credentials, send concurrent turns and prove each request
  reaches only its own model/credential.
- Verify tool schemas, handlers, approval grants and audit sinks are bound to
  the same owner under concurrency.
- Cross-process and multi-worker concurrency tests for durable leases and
  SQLite contention.
- Deployment egress firewall policy, secret redaction, keyring availability and
  cross-platform CI validation.

This is an enabling isolation layer, not a production-readiness certification.
