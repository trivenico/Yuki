# Phase 228 — Owner-isolated conversation execution contexts

## Problem

The authenticated conversation API resolves an owner for every request, but the
conversation service previously held one process-wide AgentExecutionLoop. If a
provider profile selector mutated that shared loop, one account could change the
provider used by another account's request.

## Changes

- Add an optional `agent_loop_for_owner(owner_id)` dependency to
  ConversationService, the shared application-runtime builder and the API-runtime
  builder.
- Resolve the execution loop only after conversation ownership, request
  idempotency and optimistic-version checks have passed.
- Require the factory to return a valid AgentExecutionLoop distinct from the
  shared default loop.
- Track loop-object ownership using weak references and reject a loop object
  previously assigned to a different owner.
- Keep the existing default single-loop behavior unchanged when no owner factory
  is configured.
- Add tests for per-owner routing, cross-owner loop reuse rejection and runtime
  composition.

## Owner runtime factory

The new `providers.owner_runtime_factory.OwnerAgentLoopFactory` provides the
production composition point for the authenticated API:

- Creates a new ProviderManager for each owner-loop request with an empty
  environment mapping, preventing fallback to process-global API-key variables.
- Loads only that owner's persisted profiles, reads that owner's selected
  profile, and resolves its credential through the configured OS vault.
- Builds a fresh OpenAI-compatible adapter, ProviderAgentAdapter and
  AgentExecutionLoop for the selected profile. Profile selection changes are
  observed on the next turn; no cross-owner adapter cache is used.
- Accepts an optional owner-bound GuardedToolRuntime factory. Runtime and policy
  objects are tracked weakly and cannot be reused across different owners.
- Defaults to an empty deny-all tool runtime when no owner tool-runtime factory
  is supplied. Provider configuration alone never grants tool access.

The host must pass the factory as `agent_loop_for_owner` to
`build_conversation_runtime`. Its tool-runtime callback must construct handlers,
authorization policy, approval checks and audit callbacks for the authenticated
owner. Conversation tool schemas and guarded handlers must be kept in sync by
that host integration.

## Integration contract

The host application passes OwnerAgentLoopFactory as
`agent_loop_for_owner` to `build_conversation_runtime`. The factory derives
provider selection exclusively from the authenticated owner ID supplied by
ConversationService. It fails closed when the owner has no selected, available
profile and never falls back to process-global credentials.

The host's `tool_runtime_for_owner` callback must construct handlers,
authorization policy, approval checks and audit callbacks for that authenticated
owner. Conversation tool schemas and guarded handlers must be kept in sync by
that host integration. The default factory configuration denies all tool calls.

The factory rejects cross-owner reuse of both GuardedToolRuntime and its policy,
and creates a new provider manager, vault-resolved adapter and execution loop
for each request. These checks establish object-level separation; the host still
owns the correctness of handler closures, approval binding and audit routing.

## Remaining work

- Wire OwnerAgentLoopFactory into the actual deployed API composition and add an
  end-to-end test that creates/selects two users' profiles through the
  authenticated API, then proves their conversation requests use different
  models and credentials.
- Bind the owner-specific tool runtime to the same tool schemas returned by
  `tools_for_user`; add tests proving cross-owner tool handlers, approvals and
  audit sinks cannot be reused or confused.
- Add multi-owner concurrency tests and remove the process-wide conversation
  serialization bottleneck safely. Preserve per-conversation ordering, request
  idempotency, cancellation and durable generation leases while allowing
  independent owners/conversations to execute concurrently.
- Add deployment egress firewall policy and operational monitoring as defense in
  depth for the pinned endpoint transport. DNS resolution is performed per
  request through the operating-system resolver; it must not be treated as a
  substitute for network-level egress controls.
- Complete cross-platform CI for the provider transport and owner runtime, then
  perform security review and deployment validation.


This is an enabling isolation contract, not proof that per-user provider
selection is fully integrated or production-ready.
