# DurableSkillWorker host and authorization audit

**Review date:** 2026-11-28  
**Status:** implemented worker abstraction; production host and authenticated authorization remain unverified.  
**Scope:** `core/durable_skill_worker.py`, `core/durable_task_engine.py`, `core/approved_skill_runtime.py`, `core/approved_skill_wiring.py`, `core/guarded_skill_runtime.py`, focused tests, and repository call-site inventory.

## Verified dispatch path

1. `DurableSkillWorker.run_once()` calls `engine.claim(worker_id)`. The engine claim is transactional and checks goal state, dependencies, and queue state.
2. The worker accepts only `task_type == "skill.invoke"`, a payload with exactly `skill_name` and `argument`, and `max_attempts == 1`. Unsupported tasks and malformed payloads are terminally rejected.
3. The worker invokes the injected runtime's `invoke(skill_name, argument)`; it does not import a module or resolve an entrypoint from task data.
4. `ApprovedSkillRuntime.invoke()` rechecks persistent manifest registration approval for skills wired through that facade, checks the exact reviewed instance, and delegates to `invoke_registered_skill()`.
5. `invoke_registered_skill()` checks skill registration, risk, and a process-local `OneShotSkillApprovals` token for high/critical or explicitly approval-required skills. It invokes the registered object's `execute()`/ `run()` in-process.
6. The worker persists bounded status metadata only. It does not persist returned skill data or arbitrary exception text.

## Authorization gaps — do not treat this as an owner-authorized production worker

- `DurableTaskEngine.submit()` persists task intent but has no actor identity, owner session, approval record, authorization predicate, or session-revocation check. Its `claim()` API is queue coordination, not authorization.
- `DurableSkillWorker` has no owner/session validator or authorization callback. Any host that gives it an engine and runtime can cause it to claim eligible queued work.
- `ApprovedSkillRuntime.approve_invocation()` accepts only a skill name and exact argument; it does not authenticate the caller, bind approval to an owner session, expire the approval, or write a durable invocation-approval event. `OneShotSkillApprovals` is an in-memory set, not a durable approval ledger.
- Low-risk skills whose registered metadata says they do not require approval can execute without a one-shot invocation approval. Persistent manifest approval only approves registration/wiring; it is not per-task owner authorization.
- The focused worker tests intentionally call `approvals.approve()` directly to exercise the guarded runtime. That is test setup, not evidence of an authenticated production approval UI or API.
- The skill body executes in the Yuki process with ambient OS privileges. The worker is not a process/container sandbox and does not enforce manifest-declared filesystem or network permissions.
- If heartbeat renewal fails while a synchronous skill invocation is already in progress, the worker cannot forcibly stop that invocation. It reports an error after the call returns; an external side effect may already have occurred. The engine's expired-lease recovery now prevents automatic replay, but cannot undo or reconcile the external target's result.
- The engine uses its own SQLite connections, creates parent directories, and does not accept the canonical private-profile validator or `MaintenanceFence`. It is outside the desktop/web shared-fence composition and its backup consistency guarantees.
- Engine `fail()` supports retries for generic callers. The current `DurableSkillWorker` rejects tasks whose attempt budget is not exactly one, but other worker implementations must not assume this restriction is enforced by the engine.

## Reachability finding

The inspected repository tree contains the engine, worker, guarded runtime, tests, and phase documentation. The worker accepts dependencies by injection. The current installed CLI is conversation-only and uses a deny-all tool runtime; the native desktop startup path does not construct this worker; the stock loopback web launcher does not start it. The repository search did not return indexed code references for the worker/engine symbols. This is a bounded source-tree finding, not proof that external scripts, operator services, private deployment manifests, or downstream extensions never instantiate them.

Classification: **implemented / host-owner-unverified**. Do not label it production-active, and do not assume it is unused.

## Required gate before any production host integration

1. Define one typed host composition that validates a private data root, injects one shared maintenance fence into every database writer, and owns startup, worker drain, engine close, and backup lifecycle.
2. Add an authenticated owner/session authorization check at task submission and again immediately before dispatch. Bind approval to task ID, task payload digest, skill identity/version, actor/session, expiry, and policy revision. Revalidate revocation at dispatch.
3. Separate persistent registration approval from per-invocation task approval. Do not expose a raw approval-granting method to model output, plugins, or unauthenticated callers.
4. Use a durable one-shot approval/dispatch record with atomic consumption and an explicit unknown-outcome state. Do not auto-replay after a crash or lease loss; require target-side idempotency or owner reconciliation.
5. Run consequential skills in a least-privilege isolated executor with explicit filesystem/network capabilities. In-process Python calls are not an isolation boundary.
6. Add cancellation/drain semantics that prevent new claims, track active invocations, and retain dependencies until synchronous calls unwind. Heartbeat loss must not be represented as cancellation.
7. Add adversarial tests for unauthenticated submission/approval, session revocation between approval and dispatch, duplicate claim, heartbeat loss during side effect, crash after target commit, engine shutdown during a call, backup/restore, and maintenance-fence races.

## Current decision

No host wiring was added. The worker remains an isolated injectable abstraction. Consequential execution through this path stays disabled until the authorization, isolation, lifecycle, private-profile, and reconciliation gates above are implemented and verified. No task database was opened or migrated, no worker was started, no skill was invoked, and no deployment occurred.
