# Audit 089 — Agent Execution Isolation, Worker Identity, and Runtime Security

Repository: trivenico/Yuki  
Base: main  
Scope: task runtime/store, agent lifecycle journal, sandbox policy/runtime, coding task runner, agent execution loop.  
Outcome: source audit; no runtime patch submitted.

## Executive summary

Yuki has a useful constrained-execution foundation: durable workflow state, atomic worker claims, lease-fenced completion and renewal, compare-and-swap versions, explicit sandbox policy, digest-pinned images, non-root/read-only/network-disabled defaults, bounded resources/output, and cleanup confirmation.

The main weakness is inconsistent worker identity enforcement. AgentTaskJournal validates the active lease before calling TaskRuntime.record_progress, but the lower-level progress API accepts an actor with task-progress authorization without requiring worker identity or comparing it with the active lease owner. Protection therefore depends on callers using the journal adapter and authorization callbacks being correctly scoped.

The container runner also relies on Docker CLI execution and trusted host configuration. The repository explicitly acknowledges that Docker self-reported capability metadata is not trusted attestation. A policy object or Docker info response alone does not prove host enforcement.

This is source-level analysis, not a demonstrated remote exploit or production deployment assessment.

## Files inspected

- automation/task_runtime.py
- automation/workflow_store.py
- automation/agent_task_journal.py
- automation/inference_worker_supervisor.py
- automation/local_inference_worker.py
- core/sandbox_runtime.py
- core/sandbox_policy.py
- core/coding_task_runner.py
- core/agent_execution_loop.py
- tests/test_task_runtime.py
- tests/test_agent_task_journal.py

## Positive controls observed

1. Workflow claims are serialized in SQLite and bind a worker ID to a running task lease.
2. Task completion checks running state, current worker ID, lease expiry, and expected version.
3. Lease renewal uses worker identity, expiry, and compare-and-swap checks.
4. Task transitions and audit events are committed transactionally.
5. AgentTaskJournal verifies worker lease ownership and expiry before recording progress; it fails closed on stale versions and authorization failures.
6. SandboxPolicy requires immutable capabilities, bounded timeout/memory/CPU/output/process values, read-only root, and non-root execution.
7. Default sandbox networking is disabled. Allowlist mode is refused unless a separately enforced egress gateway is available.
8. Sandbox images must be pinned by immutable SHA-256 digest; workspace is mounted read-only unless explicit write capability is granted.
9. Coding task runner requires exact boolean approval for each run, passes argv without a host shell, bounds output, applies a timeout, and requires container cleanup confirmation.
10. Docker diagnostics label daemon-reported properties as untrusted diagnostics, not attestation. Capability validation refuses to treat declarations as proof while trusted host verification is absent.
11. AgentExecutionLoop bounds model turns, tool calls, duration, transcript size, and cooperatively checks cancellation between turns and before dispatch.

## Findings

### P1 — Worker identity is not enforced by the lowest-level progress write API

AgentTaskJournal checks that the task is running, verifies lease_owner matches its configured worker ID, checks lease expiry and expected version, and then calls TaskRuntime.record_progress.

The lower-level TaskRuntime.record_progress validates task:progress authorization and that the task is running, then appends a versioned event. It does not accept a worker ID or independently compare the current lease owner and expiry. A caller with task-progress authorization can therefore write progress while another worker owns the lease, unless the authorization callback independently prevents it.

This is a confirmed enforcement inconsistency, not proof of a currently exposed exploit. A caller wired directly to TaskRuntime can bypass the journal's worker check if it has the required authorization.

Required direction: make worker-originated progress a distinct operation requiring worker ID and enforce lease owner, expiry, state, and version in one transactional store operation. Keep supervisor-authored administrative annotations separate. Audit all worker-originated mutations (artifacts, failure, completion, cancellation) for the same invariant.

### P1 — Worker identifiers are labels unless bound to authenticated worker identity

The task store persists a worker ID string as lease owner. It enforces equality against that string, but the inspected interface does not itself prove that a caller presenting the same string is the authenticated worker that originally claimed the task. Authentication and actor-to-worker binding are supplied by trusted composition around the runtime.

Required direction: authenticate worker instances independently using workload identity or mutually authenticated transport; bind worker principal to lease claim; derive worker ID from authenticated identity rather than caller-controlled input. Include audience, task, tenant, expiry, and revocation in the worker credential.

### P1 — Sandbox policy declarations are not runtime attestation

SandboxRuntimeCapabilities is diagnostic metadata and validate_runtime_capabilities explicitly notes that trusted host-side probe/attestation and verified mandatory access-control enforcement are not implemented. Docker info parsing marks namespace enforcement, network egress, mount enforcement, secret exclusion, resource limits, cleanup, and probe provenance as unsupported evidence.

Risk: a deployment could mistake configured or self-reported capabilities for proof that the actual host runtime enforces isolation.

Required direction: introduce a host-owned verifier outside the worker trust domain. Verify daemon identity, rootless mode, namespaces, seccomp profile, AppArmor/SELinux, cgroups, mounts, egress, secret exclusion, and cleanup using trusted host evidence. Fail closed when verification is unavailable or stale.

### P1 — Docker daemon access remains a privileged host boundary

The coding task runner launches and removes containers through Docker CLI. Docker daemon access can carry host-level authority depending on daemon configuration. Container flags alone do not reduce trust in the daemon endpoint or the account controlling it.

Required direction: use a dedicated rootless daemon or isolated worker VM with no production host credentials, restrict daemon socket access, and separate control plane from untrusted execution workers. Never expose the host Docker socket to agent-controlled code.

### P2 — Cancellation is cooperative and does not prove termination of all underlying work

AgentExecutionLoop checks cancellation between model turns; streaming checks between yielded events and attempts to close the stream. A synchronous provider call can still block until its adapter timeout. Closing a generator or cancelling a coroutine is not universal proof that the underlying process, thread, socket, or remote inference has stopped.

Required direction: propagate absolute deadlines and cancellation tokens to provider/worker APIs; isolate untrusted execution in killable processes/containers; confirm process/container termination and fence late completion using lease epochs.

### P2 — Resource bounds require deployment-level verification

The policy expresses output, process, memory, CPU, and timeout limits, and the runner drains output with a cap. Source alone cannot prove that Docker, kernel cgroups, runtime, and host enforce those limits under adversarial load.

Required direction: verify cgroup and kernel enforcement in a disposable worker; test fork bombs, memory pressure, CPU saturation, output floods, disk exhaustion, process escape attempts, and cleanup after daemon interruption.

### P2 — Workspace isolation depends on mount and host filesystem trust

The sandbox runtime validates an existing non-symlink workspace and resolves it before constructing the Docker mount. This is useful validation, but host-side filesystem mutation and actual mount behavior remain deployment concerns. The policy does not prove an immutable snapshot or prevent every concurrent path replacement.

Required direction: stage an immutable per-task source snapshot, mount it read-only, use a separate bounded output mount, and have the host verify actual mount configuration.

## Recommended worker execution protocol

1. Authenticated principal creates a task; tenant and owner are derived by the control plane.
2. Worker authenticates using a short-lived workload credential.
3. Claim transaction binds task, worker principal, lease epoch, and expiry.
4. Every worker write includes lease epoch and expected task version; store validates them transactionally.
5. Revocation/cancellation advances epoch and invalidates prior worker credentials.
6. Host-owned sandbox verifier attests runtime policy before execution.
7. Worker executes in an isolated VM or rootless sandbox with no host secrets and no direct control-plane credentials.
8. Host confirms termination and cleanup before accepting completion.
9. Results are size-bounded, provenance-tagged, and treated as untrusted data.

## Validation plan

- Non-owner worker cannot write progress, artifact, failure, or completion events.
- Expired/revoked lease epoch cannot write even if task remains running.
- Concurrent worker claims and writes are serialized across processes.
- Worker identity cannot be forged by choosing another worker ID string.
- Cancellation kills process/container and rejects late results.
- Runtime attestation fails closed on missing/stale/mismatched host evidence.
- Container tests cover network, mounts, namespaces, seccomp, MAC, cgroups, host secret exclusion, and cleanup.
- Adversarial load tests cover CPU, memory, PID, disk, output, and timeout limits.
- Workspace snapshot and output mounts are tested against symlink/path replacement races.

## Limitations and test status

This audit is based on source inspection of the paths listed above. No tests were executed, no exploit was demonstrated, and no production worker/daemon/host was inspected. Existing tests were inspected as evidence of intended behavior; they are not represented as passing.

No runtime patch was submitted. The worker-write fix should be implemented as a transactional lease-fenced API and coordinated with all worker callers and tests. Adding a superficial worker_id argument at the facade without store-level atomic enforcement would not close the race.
