# SQLiteTaskLifecycleStore reachability and control audit

**Review date:** 2026-11-28  
**Status:** standalone persistence primitive; no verified first-party production host.  
**Scope:** `core/task_lifecycle.py`, focused tests, CLI/desktop/API composition, worker ownership inventory, and durable-agent phase documentation.

## What the store guarantees

- Creates a SQLite task row and its initial `created` event in one `BEGIN IMMEDIATE` transaction.
- Uses a globally unique idempotency key and rejects reuse with different task type or canonical payload.
- Enforces a bounded state-transition graph and optional optimistic version check.
- Commits each state transition and its corresponding event in one transaction.
- On explicit `mark_interrupted_tasks_for_recovery()`, atomically changes all `running` rows to `recovery_required`, increments versions, and appends recovery events. It never requeues interrupted work.
- Has a global SHA-256 event hash chain and a verifier that detects changed, missing, reordered, or incorrectly chained events in the current database contents.
- Bounds JSON payload/result sizes and rejects non-finite JSON values.

This is a useful lifecycle journal, not an execution engine or exactly-once side-effect system.

## Security and operational gaps

1. **No authenticated actor/session model.** Rows have no owner, actor, session, approval expiry, policy revision, or approval digest. `transition()` trusts the caller's supplied expected state/version. Any code holding the store can move a pending task to approved and running. The state name `approved` is not proof of owner approval.
2. **No atomic one-shot dispatch claim.** The store permits `approved -> running`, but it does not bind that transition to a fresh authenticated session, handler identity/version, payload digest, or one-shot dispatch token. It does not itself invoke a handler, so callers must provide that entire boundary.
3. **Recovery is explicit, not startup-owned.** `mark_interrupted_tasks_for_recovery()` must be called by a host after it has established exclusive ownership and quiescence. No host call was established in the inspected source. A missed call leaves rows in `running`; concurrent hosts calling it can mark another live process's task as interrupted.
4. **No maintenance fence.** The constructor creates parent directories and initializes schema through direct SQLite connections. Each operation opens its own connection without the canonical `MaintenanceFence`. It is not included in the known desktop, CLI, or web shared-fence composition.
5. **Unkeyed audit chain.** SHA-256 chaining detects accidental edits and unsophisticated tampering, but a writer with database access can rewrite events and recompute the entire chain. There is no external anchor, keyed MAC, signature, or immutable remote sink. Call it an integrity check, not authenticated audit evidence.
6. **No retention, backup, or restore contract.** The database and its event chain are one SQLite file, but no store-specific backup/restore or integrity preflight is provided. Backup must use SQLite's backup API or a quiesced snapshot and verify both SQLite integrity and the event chain after restore.
7. **No execution isolation or target idempotency.** A caller can perform an external side effect and crash before `completed`/failure is committed. `recovery_required` avoids automatic replay, but the store cannot determine whether the target committed the side effect or reconcile it.
8. **Path privacy is not enforced here.** The constructor accepts a path and creates parent directories; it does not validate an owner-private root, reject symlinked ancestors, or set restrictive file permissions itself.

## Reachability and ownership

The repository's documented worker ownership audit identifies `DurableSkillWorker`, workflow supervisors, inference supervisors, reminders and outbox delivery as separate execution models. The installed CLI's conversation composition uses a deny-all tool registry; desktop startup owns the authenticated `WorkflowRunStore` and specialist coordinator; the standard API runtime owns the conversation application and explicitly injected services. None of these inspected composition roots constructs `SQLiteTaskLifecycleStore` or invokes `mark_interrupted_tasks_for_recovery()`.

The durable-agent journal phases (`PHASE142` and `PHASE143`) describe an adapter over the existing authorized `TaskRuntime` / `WorkflowRunStore`, not this independent class. Do not conflate the similarly named task journal with `SQLiteTaskLifecycleStore`.

Repository code-search returned no indexed references for the class or its recovery method. This is bounded evidence only: it does not rule out external scripts, operator services, downstream extensions, or uninspected runtime configuration. Classification: **standalone / external-host reachability unverified**. Do not claim it is production-active or unused.

## Decision and required gate

No runtime wiring is added. Keep this store out of consequential execution until a real host owner is identified and can supply:

- one canonical private profile root and shared maintenance-fence instance;
- startup recovery only after the host has acquired exclusive ownership and stopped competing writers;
- authenticated owner/session approval bound to task ID, payload digest, skill/handler identity and policy revision;
- atomic one-shot dispatch claim and explicit `outcome_unknown` reconciliation after ambiguous external effects;
- least-privilege execution isolation and target-side idempotency/fencing where supported;
- resource ownership, stop-claim/drain semantics, backup/restore validation, and keyed or externally anchored audit evidence.

## Validation and non-actions

Existing tests cover idempotency, payload mismatch, valid/invalid transitions, stale versions, interrupted-task recovery, event persistence, hash-chain tampering detection, and payload bounds. Tests were not run locally in this review. No store was instantiated against user data, no recovery method was called, no worker was started, no task was executed, no migration or deployment occurred.
