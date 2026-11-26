# Coding-task execution hardening contract

**Status:** design contract; execution remains unsuitable for unattended or privileged use.

## Source-confirmed current behavior

The current `execute_approved_coding_task()` path in `core/coding_task_execution.py`:

1. Reads a coding task and checks that its state is `approved`.
2. Re-reads the patch through `patch_integrity_ok()` and compares its digest.
3. Compares resolved task and requested workspace paths.
4. Applies an executable-name allowlist.
5. Calls a caller-supplied approval callback bound to task ID, patch digest and the exact `SandboxExecutionRequest`.
6. Runs the sandbox synchronously.
7. Generates a random run ID and persists hash-only execution evidence in a separate SQLite database.

The coding-task state machine currently permits `approved -> completed` or `approved -> cancelled`. The execution function does not atomically consume approval, reserve a run, transition the task to an executing state, or transition it to completed. It can therefore be invoked repeatedly while the task remains approved. Concurrent callers can both pass the initial state and patch checks. A cancellation can race with the interval between state check and sandbox start. The approval callback's identity/session/policy semantics are delegated to the caller and are not enforced by this module.

Evidence is persisted only after the sandbox returns. If the sandbox completes and evidence insertion fails, the caller receives an error despite a possible completed external execution. Retrying can execute the same approved task again. The evidence DB is separate from the task DB, so the two writes cannot be made atomic by ordinary SQLite transactions.

These are concrete code-level gaps, not hypothetical concerns. Existing unit tests verify request binding and basic gating, but they mock the sandbox and do not establish concurrent one-shot execution, session revocation races, crash recovery, or durable evidence reconciliation.

## Required state and transaction model

Implement a durable execution-attempt ledger in the same authoritative transaction domain as coding-task approval, or use a rigorously designed outbox/reconciliation protocol if a shared database is not possible.

Minimum task/attempt states:

- `approved`: eligible for a new, explicitly authorized attempt.
- `execution_reserved`: a unique attempt ID and immutable request digest have been committed; no worker may create a second active attempt.
- `executing`: the trusted runner has acknowledged the exact attempt and acquired its lease/fencing generation.
- `succeeded`, `failed_before_start`, `outcome_unknown`, `cancelled_before_start`: terminal attempt outcomes with distinct meanings.
- `recovery_required`: the process died or the runner disappeared while the effect may have occurred. Never automatically replay this state.

The reservation transaction must use `BEGIN IMMEDIATE` (or equivalent serializable CAS) to re-read the task version/state, verify the reviewed patch digest, verify no active attempt exists, bind the approval to a live owner session and policy revision, and insert the attempt record plus audit event. It must atomically move the task out of `approved`. Concurrent invocations must yield exactly one reservation; all others fail without launching the sandbox.

## Authorization envelope

The trusted authorization decision must bind:

- owner actor and immutable session identity/revocation generation;
- task ID and exact task version;
- patch SHA-256;
- canonical workspace identity and sandbox mount policy;
- image digest, not only a mutable image tag;
- exact argv and normalized sandbox resource/network policy;
- policy revision, approval timestamp and expiry;
- unique attempt ID, nonce and fencing generation.

The runner must not accept a caller-provided `approved=true` or a bare callback result as authority. It must validate the envelope through a trusted control-plane adapter at the protected execution boundary. Missing, stale, expired, revoked, mismatched or unavailable authority fails closed.

## Runner and crash semantics

1. Persist execution intent before starting the sandbox.
2. Pass the immutable attempt ID and fencing generation to the runner.
3. Require the runner to durably acknowledge start and return a stable execution receipt.
4. Persist terminal result/evidence idempotently under the attempt ID; retries of evidence persistence must not rerun the sandbox.
5. If the process loses contact after launch, mark `outcome_unknown` or `recovery_required`; reconcile against runner/container identity and receipt before any retry.
6. Cancellation before runner start can prevent execution. Cancellation after start is cooperative and cannot prove that external effects did not occur.
7. A lease expiry alone is not proof that the sandbox stopped. A replacement worker must fence or terminate the old runner at the execution boundary before a retry can begin.
8. Keep raw output out of durable audit records; persist bounded hashes, byte counts, exit metadata and sanitized references.

## Data and lifecycle requirements

- Prefer one durable store for task approval, attempt reservation, attempt state and audit event. If evidence blobs remain separate, the authoritative attempt row must be committed first and include an idempotent evidence reference/state.
- Apply the canonical private-profile validation and shared maintenance fence before opening every participating SQLite connection. The current coding-task and evidence stores create parent directories directly and do not accept a maintenance fence.
- Define explicit resource ownership and close behavior for both stores and the sandbox runner.
- Enforce finite wall-clock, CPU, memory, process-count, output and storage limits. Network access and mounted secrets must be denied unless the exact task policy permits them.
- Verify the sandbox implementation's isolation properties independently; an executable allowlist and container invocation are not themselves a complete sandbox guarantee.

## Required tests before re-enablement

- Two concurrent callers reserve one task: exactly one sandbox launch.
- Approval revoked or session expired immediately before reservation: no launch.
- Task cancelled or patch/version changed during reservation: no launch.
- Crash after intent commit but before runner start: safe recovery without duplicate launch.
- Crash after runner start but before receipt persistence: outcome remains unknown until reconciled.
- Runner finishes but evidence persistence fails: retry persists evidence without re-executing.
- Lease expires while old runner remains alive: new runner is fenced from executing.
- Replayed attempt ID with a different request digest: hard conflict.
- Stale policy revision, wrong image digest, altered argv/workspace or expired approval: deny.
- Maintenance mode and shutdown drain prevent new reservations and wait for active runners; timeout leaves dependencies open.
- Tests use real temporary SQLite databases and a controllable runner process, not only a mocked function.

## Enablement decision

Keep coding-task execution disabled for unattended agent workflows and privileged operations until the one-shot reservation, live-session authority, durable attempt ledger, runner fencing, crash reconciliation, shared maintenance-fence integration and failure-injection tests are implemented and verified. Human review of a patch is not by itself authorization to run it, and a sandbox result is not proof that a retry is safe.
