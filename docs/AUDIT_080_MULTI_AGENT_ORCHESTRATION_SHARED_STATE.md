# Audit 080 — Multi-Agent Orchestration, Shared State, and Coordination Security

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** agent composition, conversation orchestration, per-owner execution, role state, persistence, generation concurrency, approvals, memory, and MCP policy boundaries.

## Executive summary

The inspected implementation has useful building blocks for safe coordination: authenticated owner IDs are passed into conversation services; conversation persistence is owner-scoped; per-owner agent-loop construction is supported; generation leases coordinate concurrent workers; idempotency and recovery logic avoid replaying uncertain side effects; pending approvals bind actor/session; and MCP policies bind owner/provider/account/tool and revision.

The reviewed paths do not establish a complete general-purpose agent-to-agent orchestration system with first-class agent identities, signed delegation, message authorization, shared-state ACLs, durable task ownership, or cross-agent conflict resolution. This is a scoped source audit, not proof that no such code exists elsewhere in the repository.

## Confirmed finding

### P1 — Conversation role state is shared across owners

`ConversationService` constructs or accepts one `RoleEngine` instance and stores it as `self.role_engine`. Its `set_role(name)` method changes that shared instance, while `_system_guidance()` reads the current shared role when preparing a conversation.

The service also handles requests for different authenticated `owner_id` values. Therefore, when multiple users share one service instance, a role change made through the service affects subsequent response guidance for every user using that instance. The role engine's own documentation correctly says roles do not grant tools or permissions; the issue is cross-user context/style isolation, not privilege escalation.

**Remediation:** move role selection to owner-scoped state. Resolve the role using the authenticated owner for every request, persist it if it must survive restarts, and avoid a mutable process-global default for authenticated multi-user traffic. Preserve a separate local/default role for single-user CLI mode. Add concurrent tests proving owner A's role cannot affect owner B.

No runtime patch is submitted in this audit: the correction needs a clear owner-scoped role API and persistence contract, and the inspected API does not expose role selection as an endpoint. A partial in-memory map would not survive restarts or coordinate multiple workers.

## Existing positive controls

- `ConversationStore` accepts an owner ID and scopes conversation reads/writes by owner.
- `ConversationService` accepts owner identity from its caller and does not authenticate network requests itself.
- `agent_loop_for_owner` allows construction of an owner-specific execution loop; its result is type-checked.
- Owner-specific loops are resolved only after ownership, version, interrupted-generation, and replay checks.
- Conversation generation uses a durable lease with heartbeat renewal and token-fenced release.
- Generation request IDs and message idempotency keys support safe retry/replay behavior.
- Recovery logic explicitly avoids re-running uncertain model/tool sequences after interrupted execution.
- Pending approvals bind actor and session and are atomically consumed once.
- MCP policies are scoped to owner/provider/account/tool, check required OAuth scopes, and fence stale policy revisions.
- MCP write/financial actions require literal-True owner confirmation and audit; read-only actions still require an enabled policy and scopes.

## Additional architecture gaps

### P1 — No first-class agent identity in the inspected conversation contract

The conversation service selects an owner-specific loop, but the reviewed call contract does not carry a distinct agent identity, workload identity, or signed delegation context into every tool dispatch. Owner identity and agent identity are different security principals and should not be conflated.

### P1 — Generic agent-to-agent delegation contract not demonstrated

The reviewed approval coordinator binds actor/session, and MCP policies bind external account context. Neither establishes a general signed, attenuating delegation grant between agents with issuer, subject, audience, tenant, actions, resource scope, expiry, policy revision, and revocation.

### P1 — Shared memory has no owner/agent namespace in its basic API

`VectorMemory` delegates to `MemoryStore`, whose basic `remember/search/list` contract is a local file store without an owner or agent argument. This is appropriate for a single-user local store, but it is not a safe shared-memory API for multiple authenticated users or agents unless each principal receives a separate store/path and that isolation is enforced by trusted composition. Do not mount one shared instance as a cross-tenant memory service.

### P2 — No general task queue/event bus was identified in the inspected candidate paths

The expected conventional paths `core/agent_orchestrator.py`, `core/orchestrator.py`, `core/task_queue.py`, and `core/event_bus.py` were not present at those exact paths. This does not prove no equivalent exists elsewhere. A repository-wide inventory is required before designing or adding a second queue/event abstraction.

### P2 — Process-local locks are not the distributed coordination primitive

Conversation service uses a process-local reentrant lock in addition to durable database leases. The database lease is the cross-instance coordination primitive for a conversation. Future agent task coordination must use durable claims, lease expiry/renewal, fencing tokens, idempotency, and explicit terminal states rather than relying on Python locks.

### P2 — Shared role state is not the only shared mutable state to review

The service-wide `RoleEngine` is a concrete example. Audit all mutable providers, model selectors, tool registries, cancellation maps, persona settings, memory stores, and agent configuration for accidental process-wide state in authenticated multi-user deployments. Server-wide provider selection is intentionally administrator-controlled and must remain distinct from per-user preferences.

## Recommended Yuki orchestration architecture

1. **Central orchestrator:** owns task planning, dispatch, deadlines, retries, cancellation, and completion.
2. **Agent registry:** stable agent IDs, immutable capability profiles, versioned configuration, lifecycle and health.
3. **Delegation service:** signed, short-lived, attenuating grants; no agent can mint broader authority than it holds.
4. **Task ledger:** durable task IDs, parent/child links, owner/tenant, assigned agent, status transitions, idempotency, lease fencing, and recovery.
5. **Message bus:** authenticated producer/consumer identity, bounded schemas, correlation/causation IDs, replay protection, and per-topic ACLs.
6. **Memory gateway:** explicit owner/tenant/agent namespace, provenance labels, read/write scopes, retention, and deletion propagation.
7. **Shared state:** transactional writes, version checks, conflict policy, and explicit ownership; no unscoped global mutable context.
8. **Final action gate:** revalidate identity, delegation, resource ownership, current policy revision, approval, and audit immediately before side effects.
9. **Observability:** trace parent/child tasks and agent handoffs without logging secrets or treating model-generated identity claims as trusted telemetry.

## Required regression and adversarial tests

- Two authenticated owners select different roles concurrently; each receives only its own guidance.
- One agent cannot read or overwrite another agent's private memory namespace.
- Delegated agent cannot exceed the parent's action/resource scopes.
- Revoked or expired delegation is denied at the final tool boundary.
- Duplicate task delivery cannot repeat a side effect.
- Stale worker cannot commit after lease expiry/reassignment.
- Cross-tenant task IDs, message IDs, and memory references are rejected.
- Agent messages with forged sender identity or replayed correlation IDs are rejected.
- Crash recovery resumes only idempotent/reconcilable work and never blindly replays uncertain side effects.

## Validation limits

Source inspection only. No runtime code was changed and no tests were executed. No distributed multi-agent deployment, cross-process message bus, concurrent role-isolation test, shared-memory tenant test, worker crash test, or delegation-signature implementation was exercised. This audit does not claim that Yuki currently has a production-ready 100-agent orchestration plane.
