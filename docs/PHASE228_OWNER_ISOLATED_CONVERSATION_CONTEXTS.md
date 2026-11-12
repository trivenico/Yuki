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

## Integration contract

The host application supplies the owner-loop factory. It must derive the
provider profile only from the authenticated owner ID passed by ConversationService,
construct a fresh or owner-cached adapter and AgentExecutionLoop, and preserve
the same guarded tool policy, approval service, capability checks and audit
hooks. It must never accept an owner ID from prompt text or request JSON.

The service enforces object-level separation, but cannot prove that a caller's
factory has not embedded a shared mutable provider object inside two distinct
AgentExecutionLoop instances. The factory implementation still requires a
security review and integration tests before enabling profile-based chat.

## Remaining work

- Implement and test the production owner-loop factory that resolves
  ProviderProfileStore selection and OS-vault credentials for the authenticated
  owner.
- Apply connection-time DNS/IP destination pinning and redirect rejection to
  custom endpoint adapters. The OpenAI-compatible adapter now resolves the approved
  hostname, rejects the entire DNS answer if any address is non-public, connects to
  one of those validated IPs directly, and retains TLS certificate/SNI validation
  against the original hostname. Ambient proxy settings are bypassed because they
  would invalidate destination pinning. Requests are limited to HTTPS port 443 and
  no redirects are followed. The same transport is used by text, chat, streaming,
  and tool-call request paths.
- Add regression tests for private and mixed public/private DNS answers, pinned
  socket destinations, TLS hostname preservation and nonstandard-port rejection.
- Ensure per-owner tools/approval contexts are independently bound, not merely
  per-owner model adapters.
- Add concurrency tests across owners and benchmark worker utilization; the
  existing ConversationService lock currently serializes turns in one process.

This is an enabling isolation contract, not proof that per-user provider
selection is fully integrated or production-ready.
