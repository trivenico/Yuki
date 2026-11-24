# Audit 085 — Agent Runtime, Task Orchestration, and Autonomous Execution Safety

**Repository:** `trivenico/Yuki`  
**Base reviewed:** `main`  
**Scope:** specialist-agent registry/coordinator, task runtime, workflow engine/store, task journal, dependency dispatch, cancellation, and associated tests.

## Executive summary

Yuki has a substantive orchestration foundation: specialist agents are registered with bounded capability/permission manifests; routing is deterministic and least-overprovisioned; plans validate dependency graphs and cycles; plan submission is idempotent; tasks are durably enqueued; dispatch is dependency-gated; prior-agent outputs are marked as untrusted and bounded; workflow execution uses explicit approval; worker claims are lease-fenced; task progress and terminal transitions are versioned and journaled.

The review identified important gaps around plan ownership, concurrent dispatch/cancellation, and permission revalidation at the point work is executed. These require a coordinated durable orchestration contract rather than a superficial local patch.

## Findings

### P1 — Agent plans are not durably bound to their creating actor/tenant

The `agent_plans` schema stores plan ID, idempotency key, request digest, and creation time, but no owner/tenant/creator principal. Plan IDs and idempotency keys are derived globally from the supplied idempotency key, and the idempotency key has a global unique constraint. `get_plan` delegates access to an authorization callback with the plan ID, but the plan record itself cannot independently enforce creator/tenant ownership.

This means cross-principal isolation depends entirely on every caller's authorization callback being perfectly scoped. A globally reused idempotency key also creates cross-owner collisions: a second actor can collide with or receive the first actor's plan record when request digests match.

**Recommendation:** persist immutable owner/tenant and creator identity on every plan; scope idempotency uniqueness and plan ID derivation to tenant/owner; enforce owner/tenant predicates in every read, advance, and cancel query; derive actor identity from authenticated context rather than request fields. Add migration/backfill behavior that fails closed for legacy records whose owner cannot be established.

### P1 — Dispatch and cancellation do not have a single atomic plan-level state boundary

`advance` snapshots subtask rows, creates runtime tasks outside the coordinator database transaction, then separately updates plan-subtask rows. `cancel_plan` also snapshots rows and separately cancels runtime tasks or marks pending subtasks cancelled. Stable task idempotency keys make duplicate advances safer, but the coordinator does not persist a plan-level cancelled state or a dispatch reservation/epoch that atomically arbitrates these operations.

A concurrent advance can act on a stale pending snapshot while cancellation is marking subtasks cancelled. Depending on interleaving, newly created work may be left queued or coordinator state may be overwritten inconsistently.

**Recommendation:** add a durable plan state and monotonically increasing dispatch/cancellation epoch. Claim a subtask dispatch reservation with compare-and-swap before enqueue; after enqueue, attach the task only if the reservation/epoch remains current. Cancellation atomically closes dispatch admission, then reconciles every reservation/task ID. Add deterministic barrier-based concurrency tests across separate coordinator instances sharing the database.

### P1 — Capability/permission checks occur at plan routing, but task execution does not carry a signed attenuated agent grant

The coordinator verifies required capabilities and permissions against a specialist registry when planning. It then enqueues a workflow task with action, argument, and approval flag. The persisted task definition does not include a cryptographically verifiable agent identity, issuer, audience, tenant, permitted resource scope, permission set, expiry, or registry/policy revision. The task runtime authorizes the actor/task operation, while the workflow worker executes the stored action.

This separates the planning-time specialist manifest from the execution-time authorization boundary. A registry change, permission revocation, or task reassignment is not represented as an independently verifiable attenuated agent grant in the task record.

**Recommendation:** attach a signed, short-lived, privilege-attenuating delegation grant to the task. Bind principal, agent ID, tenant, task ID, action, resource scope, permission set, policy/registry revision, issue/expiry times, and revocation reference. Verify it at worker claim and immediately before side effects. A delegated agent must never gain more authority than the initiating principal.

### P2 — Specialist registry manifests are global rather than tenant-scoped

The `specialist_agents` table is keyed only by agent ID and stores capabilities, action, permissions, and enabled state. The reviewed coordinator therefore models one global registry for its database, not separate per-tenant agent registries or tenant-specific configuration/credential bindings.

**Recommendation:** scope agent registration and registry queries by tenant/workspace. Keep executable code and credentials out of registry manifests; bind runtime identity and tool access through separately governed policy.

### P2 — Dependency output is bounded and marked untrusted, but provenance is not typed

Dependency output is appended to downstream task argument text with an explicit untrusted-data warning and a 2,000-character cap. This is a useful prompt-injection mitigation, but the task payload does not preserve structured source task IDs, content hashes, trust class, or provenance references as first-class fields.

**Recommendation:** use typed dependency artifacts with source task ID, owner/tenant, content hash, trust classification, and immutable reference. Preserve provenance through compaction and model context assembly; never treat text labels as an authorization boundary.

### P2 — Plan/task state reconciliation is not one transaction

The coordinator and task runtime may use separate SQLite files or the same database. The implementation intentionally avoids holding a coordinator write lock while calling TaskRuntime, and uses deterministic idempotency keys to recover from partial writes. This is a sensible deadlock-avoidance tradeoff, but it leaves a saga/reconciliation contract implicit: task enqueue can succeed while coordinator attachment fails, or cancellation and task completion can race.

**Recommendation:** formalize an outbox/saga state machine with durable dispatch intent, task correlation ID, idempotent attach, retryable reconciliation, and observable orphan detection. Document recovery operations and ensure they are owner-scoped.

## Existing positive controls

- Specialist manifests are bounded and validate identifiers, capabilities, permissions, and allowed action.
- Routing is deterministic and chooses the least-overprovisioned enabled agent.
- Plan DAGs reject duplicate IDs, unknown dependencies, self-dependencies, and cycles.
- Plan submission validates idempotency key and canonical request digest.
- Task enqueue uses stable idempotency keys to make repeated dispatch safer.
- Dependencies must complete successfully before downstream task creation.
- Prior specialist output is explicitly labelled untrusted and bounded before inclusion.
- Workflow actions are allow-listed and approval is exact-true/fail-closed.
- Workflow workers atomically claim queued work and use lease ownership/version checks.
- Task transitions and their audit events can be committed atomically.
- Agent lifecycle journal records bounded metadata and refuses stale/expired worker leases.
- Cancellation uses versioned task transitions and reconciles concurrent task completion.

## Recommended JARVIS-style orchestration architecture

1. **Principal/tenant context:** derive from authenticated user/session or trusted workload identity.
2. **Agent registry:** tenant-scoped, versioned manifests with explicit capabilities and policy revisions.
3. **Planner:** produce a bounded, validated DAG; no model-generated authority.
4. **Delegation authority:** issue signed attenuated grants per task and specialist.
5. **Durable plan state:** plan lifecycle, task correlations, dispatch epochs, cancellation fence.
6. **Scheduler:** dependency-aware admission, quotas, priorities, and resource requirements.
7. **Worker gate:** revalidate grant, tenant, approval, policy revision, lease, and resource budget before side effects.
8. **Artifact/provenance store:** typed immutable outputs and trust metadata.
9. **Saga recovery:** durable outbox, reconciliation, orphan detection, retry budget, and dead-letter handling.
10. **Audit/observability:** plan lineage, delegation chain, decisions, tool side effects, and safe metadata without prompts/secrets.

## Required regression/adversarial tests

- Two owners using the same idempotency key cannot collide or read each other's plans.
- Plan reads, advance, and cancellation reject wrong tenant/owner even if the caller has generic agent permissions.
- Concurrent `advance` and `cancel_plan` cannot leave a queued task attached to a cancelled plan.
- Two coordinator processes racing dispatch create one task and converge to one subtask state.
- Agent disable/revocation between planning and worker claim blocks execution.
- Permission reduction or policy revision invalidates stale delegation grants.
- Worker cannot use a grant for another task, tenant, resource, action, or expired session.
- Downstream dependency artifacts retain immutable provenance and untrusted classification.
- Partial coordinator/task-store failures are reconciled without duplicate side effects.
- Retry and recovery respect cancellation epochs and task idempotency.

## Validation limits

Source inspection only. No runtime code changed and no tests were executed. Existing tests were inspected for DAG validation, idempotency, dependency gating, untrusted-output framing, agent enable/disable, cancellation, and worker lease behavior. No concurrent multi-process stress, cross-tenant API integration, signed delegation verification, worker side-effect integration, or crash-recovery chaos test was run.
