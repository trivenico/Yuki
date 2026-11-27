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
- Apply the canonical private-profile validation and shared maintenance fence before opening every participating SQLite connection. The coding-task and evidence stores now require an injected `MaintenanceFence`, hold it through each connection's commit/rollback and close, and reject database paths outside the fence root. `build_coding_task_stores()` validates the private profile and injects one shared fence into both stores. This is maintenance coordination only: the two SQLite databases still do not share an atomic transaction, and no production host wiring has been confirmed.
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


## Additional sandbox backend findings (2026-11-26)

A source review of `core/coding_task_runner.py` and `core/sandbox_runtime.py` found that the current runner builds and launches Docker argv directly. Although `SandboxRuntimeCapabilities` and `validate_runtime_capabilities()` exist, the execution path does not call the capability validator and no trusted host-side verifier is implemented. Docker daemon self-reported security options are explicitly diagnostic only. Therefore the runner has no verified proof that the daemon is rootless, namespaces/seccomp/MAC are enforced, cgroup limits are effective, or host secrets are excluded. Refusing to run the service as UID 0 is not proof that the Docker daemon itself is rootless.

The generated Docker command does apply useful controls: immutable image digest syntax, `--network none`, read-only root filesystem, read-only workspace by default, resource flags, PID limit, dropped Linux capabilities, `no-new-privileges`, and bounded output/time in the client. These are defense-in-depth settings, not a complete isolation attestation. The command uses a bind mount of the entire reviewed workspace. A workspace may contain sensitive files or Unix-domain sockets (including a Docker socket); those objects are exposed inside the container according to mount permissions. A preflight filesystem walk alone would still be vulnerable to replacement races, so a safe future design should execute against a dedicated immutable/staged workspace snapshot with an explicit file policy, not mount an arbitrary live owner workspace.

The runner's cleanup path calls `docker rm --force` and fails closed if cleanup cannot be confirmed, which is positive. However, client process termination, container removal and proof that no descendant/external side effect survives are distinct properties. A runner receipt and host-side container identity/reconciliation are still required for safe retries after process loss.

### Sandbox enablement gate

Do not treat the existing Docker flags or diagnostic capability parser as host attestation. Before enabling any coding-task sandbox execution, implement a trusted deployment-owned verifier that checks the actual daemon/runtime configuration and effective per-container controls, including rootless operation, user/PID/mount/IPC/network namespaces, seccomp, AppArmor/SELinux, cgroups v2/resource enforcement, network isolation, workspace mount contents, secret exclusion and cleanup. Bind the verified runtime identity/config digest to the execution authorization envelope. If verification is unavailable or stale, refuse execution before creating a container.

The current runner is therefore a constrained Docker invocation prototype, not a verified production sandbox. Keep execution disabled for unattended or privileged tasks.


## Maintenance-fence implementation update (2026-11-27)

The coding-task and execution-evidence stores now participate in the shared
maintenance fence as described above. The new composition factory and
regressions are present in the assessment branch. This closes the store-level
maintenance-lock bypass for callers using the supported factory; it does not
prove production reachability, cross-database atomicity, or safe runner
reconciliation. Execution remains disabled for unattended and privileged
workflows pending the separate attempt-ledger, live-session, runner-attestation,
and crash-recovery gates.
