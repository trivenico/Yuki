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


## Live output streaming

The post-exec monitor now starts `docker logs --follow` for the exact daemon
container ID and drains stdout/stderr concurrently while the workload runs.
Combined retained output is bounded by the lower of the monitor and sandbox
policy ceilings. Once the ceiling is exceeded, the log follower is stopped and
the workload container is removed; the result is marked truncated and the
executor fails the task. The same follower loop checks cancellation/lease
status and the wall-clock deadline, terminating the workload on cancellation,
lease-check errors, timeout, or output overflow. After normal log-stream
completion, a final inspect verifies the exact full container ID, stopped state,
OOM flag and exit code.

The log follower itself uses a process group and bounded termination grace.
This is still subject to real Docker integration testing, especially CLI
stream termination semantics, daemon disconnects, and PID/cgroup cleanup. It
does not replace the independent outer finally cleanup.


## Specialist worker integration seam

`GateFirstSandboxExecutorAdapter` implements the specialist worker's
`execute_authorized_task()` protocol. It rechecks the task's registered spec,
worker lease and `task:sandbox:execute` authorization before allocating gate
resources, forwards the worker's lease/cancellation predicate, and never falls
back to the direct executor. The gate-first executor publishes
`task.sandbox.started` only after the collector returns from the session's
exact-argv exec observation; completion/cancellation/failure status is then
reported through the existing lifecycle callback.

`build_registered_specialist_worker()` now accepts explicit
`sandbox_executor` and `sandbox_profiles` injection. An operator-controlled
factory can pass a `GateFirstSandboxExecutorAdapter` there while preserving
the current default (no sandbox executor). This is an integration seam, not a
complete deployment factory: Docker Engine API credentials, trusted host
evidence builder, owner resolver, identity providers, rootless/user-namespace
mapping and release publisher still require operator-owned composition and
real-host validation. Gate-first remains opt-in and is not automatically
enabled by the worker service.


## Worker-service deployment mode

The dedicated worker service accepts
`YUKI_SANDBOX_EXECUTION_MODE=direct|gate_first`. The default remains
`direct` for backward compatibility; operators must explicitly select
`gate_first` to enforce the gated path. In gate-first mode, the service
validates the complete returned `SpecialistWorkerSupervisor` before installing
signal handlers, registering workers or claiming tasks. Every registered
worker must use `GateFirstSandboxExecutorAdapter` and have a non-empty,
spec-ID-keyed immutable sandbox profile registry. A mixed supervisor, direct
executor, missing profile, malformed profile mapping or wrong factory return
type aborts startup. There is no fallback.

The operator-supplied `YUKI_WORKER_SUPERVISOR_FACTORY` remains responsible for
constructing the concrete Docker Engine API client, trusted host identity and
evidence providers, release publisher, gate-first executor, adapter, profiles,
worker registry and supervisor. This repository now validates the resulting
composition but does not invent deployment-specific host credentials or
evidence providers. Keep this factory in a root/service-managed module path,
not a task workspace or user-writable directory.

Example service environment:

```ini
YUKI_WORKER_SUPERVISOR_FACTORY=yuki_deployment.worker:create_supervisor
YUKI_SANDBOX_EXECUTION_MODE=gate_first
```

The existing Docker/rootless/user-namespace and live-host integration checks
remain mandatory before enabling this mode in production.


## Host preflight is mandatory for gate-first startup

`GateFirstDockerSandboxExecutor` now accepts an operator-owned `host_preflight` callback. The gate-first supervisor validator invokes `validate_deployment_host()` for every registered executor before the worker service starts. Missing callbacks, callback exceptions, incomplete reports, or a report whose service UID / canonical Docker executable differs from the executor abort startup.

`core.gate_first_host_preflight` provides the host-side validation building block. It checks Linux-only operation, a pinned SHA-256 identity for the Docker executable, executable ownership and write permissions, a local service-owned Docker Unix socket, a private service-owned gate directory, procfs mount/visibility and the worker's UID map. It also requires two explicit operator verifiers to return literal `True`: one for the actual Docker user-namespace / bind-mount / peer-credential UID mapping, and one for release publisher ownership, immutable record-store ownership and key custody.

The preflight deliberately does not guess rootless mappings from the worker's `/proc/self/uid_map`, infer publisher trust from a callable's name, or claim hardware attestation. The operator factory must compose the trusted callbacks against the actual daemon and publisher configuration. The preflight report is an internal startup gate, not a cryptographic attestation document.

This is still not permission to enable gate-first in production: run the new regression tests, exact-head CI, security review, and real Docker integration tests for the supported rootful/rootless deployment modes first.


## Concrete operator composition factory

`core.gate_first_deployment_factory.build_gate_first_sandbox_executor()` now wires the existing read-only `DockerEngineAPI`, bounded `DockerGateContainerClient`, `DockerEngineSupervisorCollector`, and `GateFirstDockerSandboxExecutor` into one executor. The operator must pass explicit trusted callbacks for host identity, task-owner binding, runtime evidence, immutable release-record publication, gate PID/UID resolution, exact exec observation, user-namespace mapping, publisher/key/store ownership, observer configuration, procfs visibility, and Linux capability validation. There are no insecure callback defaults.

The factory validates callback presence before constructing Docker clients. It creates no worker, listener, container, or background thread. Its mandatory `host_preflight` runs when `validate_gate_first_supervisor()` validates the configured workers, before the service starts worker registration or task claims. The caller still builds each worker with `GateFirstSandboxExecutorAdapter` and a non-empty immutable spec-to-profile mapping. The factory does not grant agent capabilities, approve tasks, or activate gate-first mode on its own.

The factory intentionally accepts the already-reviewed `RuntimeEnvironmentEvidence` builder and release publisher rather than silently guessing a host observer or key-store implementation. The deployment's operator-owned verifier callbacks must inspect the real daemon configuration and publisher/key custody; returning literal `True` without checking those facts defeats the security boundary.


## Registered specialist worker integration

`automation.specialist_worker_factory.build_gate_first_registered_specialist_worker()` is the dedicated construction path for gate-first workers. It requires an actual `GateFirstDockerSandboxExecutor` and a non-empty, bounded mapping of immutable sandbox profiles keyed by their exact `spec_id`; it creates the `GateFirstSandboxExecutorAdapter` itself and delegates the existing registry, provider, role and permission checks to `build_registered_specialist_worker()`. Callers cannot override the adapter or profile arguments through the generic options.

The specialist worker's bounded parallel pool now propagates the configured sandbox executor and immutable profile registry to each child worker. Child workers retain their own lease owner and durable registry session; the adapter still validates the task lease against that child identity before authorization or container creation. This prevents parallel execution from silently dropping the gate-first path (previously it failed closed because child workers lacked profiles/executor).

The supervisor still performs host preflight before calling `run_until_shutdown()`, and the normal worker task path still checks the persisted execution-spec digest, owner, requested capabilities, task lease, and `task:sandbox:execute` authorization before invoking the adapter. These changes wire construction and parallel propagation; they do not replace runtime policy or host integration testing.


## Terminal lifecycle audit on execution failure

Once the executor has successfully emitted `task.sandbox.started` after same-PID exact-argv exec observation, it now attempts exactly one terminal lifecycle event. A normal monitor result records completed, failed, or cancelled as appropriate. If cancellation is observed immediately after release, or the post-exec monitor raises/returns invalid evidence, the executor attempts a failed/cancelled terminal event with no untrusted result payload before cleanup. A terminal-event callback failure remains fail-closed and is surfaced as an audit failure; the executor does not silently claim a complete lifecycle. The event uses the same daemon-assigned container ID as the start event.


## Task-lease loss during a live sandbox

The specialist worker supplies a cancellation predicate that returns true when its task lease heartbeat has failed, the task is no longer running/owned by that worker, the lease expiry cannot be parsed or has elapsed, or the registered worker session guard cannot prove current ownership. The gate-first executor checks cancellation before container creation, after the gate release, and passes the predicate into the bounded post-exec monitor. A live monitor must stop/remove the container when cancellation is observed. The worker discards any returned sandbox output after cancellation and does not complete the task. A regression test now forces lease renewal failure during a sandbox run and asserts cancellation plus the `started`/`cancelled` audit pair, with no committed result.
