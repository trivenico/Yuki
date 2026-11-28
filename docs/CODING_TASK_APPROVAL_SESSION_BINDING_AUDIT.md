# Coding-task approval session binding audit

**Review date:** 2026-11-28  
**Scope:** `core/coding_task_execution.py`, `core/coding_tasks.py`, `core/auth.py`, `core/authenticated_approval_gateway.py`, and approval composition.

## Finding

The coding-task executor consumes a task's approved state atomically, but its execution authorization callback is not intrinsically bound to a live owner session or revocation state.

In `execute_approved_coding_task()`, the callback contract is:

```python
approve: Callable[[str, str, SandboxExecutionRequest], bool]
```

The callback receives the task ID, reviewed patch digest, and exact sandbox request. If it returns the literal boolean `True`, the executor immediately calls `claim_execution()` and then returns approval to the sandbox runner, which can proceed to Docker launch.

The executor itself does not receive an authenticated actor, session ID/token, session-token provider, revocation generation, or live identity validator. It does not call `BuiltInAuthService.resolve_session()`. Therefore the task-state claim protects against concurrent/replayed task execution, but does not itself prove that the owner session which authorized the action remains live at the execution boundary.

This is an integration contract gap, not proof that a particular deployment currently exposes an exploitable path. A trusted callback could perform live session checks, but the executor API does not require or verify that behavior, and a one-time callback result cannot close the race between callback validation and the subsequent sandbox side effect.

## Important distinction

The separate guarded approval subsystem has stronger explicit identity plumbing: `AuthenticatedApprovalGateway` resolves the current session at confirmation, and `GuardedApprovalExecutor` constructs a guarded runtime with an identity validator that checks the current token/session again at invocation. The coding-task execution path does not use that guarded dispatch path and has no equivalent required validator in its function contract.

The existing atomic `approved -> executing` claim and non-replay behavior are positive controls. This finding does not restate the already documented attempt-ledger, evidence reconciliation, Docker attestation, or workspace snapshot gaps.

## Required remediation

1. Replace the bare boolean callback contract with a trusted authorization adapter that returns a structured, immutable authorization envelope.
2. Bind the envelope to owner ID, session identity/revocation generation, task ID/version, patch digest, exact sandbox request, policy revision, expiry, and a unique attempt ID.
3. Revalidate the live owner session and authorization envelope at the protected runner boundary immediately before container creation; do not rely solely on an earlier UI/callback check.
4. Make revocation and execution reservation semantics explicit. Where a cross-store atomic transaction is unavailable, use a durable reservation/outbox protocol and define which operation wins when revocation races reservation.
5. Add tests for expired/revoked sessions before approval, between callback and reservation, and between reservation and process launch. Assert that no sandbox process starts when the authorization is stale.
6. Keep coding-task execution disabled for unattended or privileged workflows until this contract and the independent sandbox-attestation/reconciliation gates are implemented and verified.

## Validation limits

Source inspection only. No runtime code changed, no local tests run, no Docker daemon contacted, and no container created or removed. This review does not establish that the coding-task executor is wired into a production host.


## Confirmed revocation-to-launch race window (runner sequence)

A branch-head trace of `core/coding_task_runner.py::run_sandboxed_task()` confirms the precise ordering:

1. Build and validate Docker argv.
2. Construct the immutable `SandboxExecutionRequest`.
3. Call `approve(request)` once.
4. If it returns exactly `True`, generate the container name and prepare argv.
5. Call `subprocess.Popen(...)` to start the Docker CLI.

In the coding-task adapter, step 3 invokes the caller's Boolean callback and then atomically changes the task from `approved` to `executing`. Once that callback returns, the runner performs no further session or revocation validation before `Popen`. Consequently, revocation occurring after the callback's session check—including after the durable task claim but before Docker process creation—does not stop this launch path. The race is source-confirmed; whether an attacker can reliably time or exploit it depends on deployment concurrency and authority over session revocation. It is primarily a stale-authorization / revocation-semantics gap, not proof of arbitrary code execution.

The separate guarded approval executor's second identity check does not protect this runner path because the coding-task runner does not invoke it. Remediation must place a trusted, revocation-aware authorization check at the runner's protected launch boundary and define atomic ordering between revocation and execution reservation; a second best-effort check alone still needs explicit race semantics.
