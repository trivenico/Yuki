# Gate-first Docker executor orchestration

`GateFirstDockerSandboxExecutor` is an opt-in orchestration boundary separate
from the existing `DockerSandboxExecutor`. It composes the existing gate
authorization session, gate-only Docker create/start/remove client, trusted
supervisor collector, and an operator-provided post-exec monitor.

## Ordered lifecycle

1. Validate the running task lease, registered execution spec, profile and
   sandbox budget before creating resources.
2. Create a fresh mode-0700 directory under a service-owned mode-0700 root.
3. Open a fresh `ExecutionGateAuthorizationSession` before Docker create.
4. Build a gate-only create argv with the session's fixed socket path and
   immutable startup arguments. The task command is not included in create.
5. Create and start the gate container using its full daemon-assigned ID.
6. Construct the trusted supervisor collector with the session's identity
   binder and one-shot releaser. The collector validates labels, image digest,
   resource limits, live evidence and stable PID before releasing the gate.
7. Require the session to observe the same PID transition to the exact pinned
   argv. Only then invoke the required post-exec monitor.
8. Close the authorization session, remove the container by its unique name,
   and remove the private directory on every outcome. Cleanup uncertainty is a
   hard failure requiring operator reconciliation.

There is no fallback to the direct executor.

## Mandatory deployment contract

The injected `supervisor_collector_factory` must return a collector configured
with the operator-owned Docker Engine API, release publisher, host evidence
builder, host identity provider and owner resolver. It must preserve the
collector's default release-provenance requirements.

The injected `post_exec_monitor` is mandatory. It must monitor the exact
created container and enforce bounded stdout/stderr capture, wall-clock
deadline, cancellation and lease-loss handling, process/container exit
observation, and return a `SandboxExecutionResult` whose container ID matches
the created daemon ID. It must not execute a command, restart a container, or
fall back to direct execution. The orchestrator independently removes the
container in its `finally` path.

## Not yet production-enabled

This orchestration layer is an integration seam, not a claim of production
readiness. No production worker-service factory is wired to instantiate it.
The concrete post-exec Docker monitor still needs implementation and tests.
Rootless/user-namespace bind-mount ownership, socket access and SO_PEERCRED UID
mapping remain unvalidated against real Docker deployments. The new tests are
regression tests only and have not been executed in this environment. Do not
enable this path in production until monitor integration, real Docker
integration tests, exact-head CI and security review succeed.


## Built-in post-exec monitor

The executor now defaults to `DockerGatePostExecMonitor`. After the session has
observed the gate PID become the exact pinned argv, the monitor polls Docker
inspect for the same full container ID until exit, cancellation/lease loss, or
the policy deadline. It rejects malformed inspect responses and invalid exit
codes, then retrieves container stdout/stderr only after exit. Captured output
is capped at 64 KiB combined (or the lower policy ceiling); exceeding the
ceiling is reported as truncated and the orchestrator fails the task. The
monitor never starts or execs a process. The outer executor remains responsible
for final container and private-directory cleanup.

This uses bounded Docker CLI calls and is a first integration implementation,
not yet a production claim. It requires real Docker validation for daemon
inspect/log behavior, cancellation races, OOM and timeout handling. It does not
yet stream output while a task is running; output is collected after exit.
