# Evolution sandbox supervisor

`automation.evolution_sandbox_supervisor.EvolutionSandboxSupervisor`
supervises a prepared `SandboxExecutionSpec`. It is a process supervisor
around the existing restrictive Docker argv builder; it is not a replacement
for the host's container isolation boundary.

## Runtime deployment requirements

- Run on a dedicated, disposable worker or VM.
- Prefer rootless Docker/Podman or a VM-backed container runtime.
- Never expose a privileged Docker socket to agent-controlled code. Control
  of a rootful Docker daemon is effectively host-root access.
- Run the Yuki supervisor service as a non-root user. The current Docker argv
  builder deliberately refuses to build a launch command when the service
  runs as root.
- Pin runner images by immutable SHA-256 digest and pre-pull them through a
  trusted image promotion process. The supervisor uses `--pull=never` so
  execution cannot trigger an unreviewed network pull.
- Keep worker credentials and host mounts out of the container.

## Enforced by this supervisor

- Revalidates the prepared spec and workspace containment immediately before
  launch.
- Requires a digest-pinned image reference.
- Requires validated policy, disabled networking, and explicit workspace
  write capability.
- Passes argv as a sequence; it does not invoke a shell.
- Adds a Docker container ID file and disables implicit image pulls.
- Captures stdout and stderr concurrently with a combined byte ceiling.
- Enforces the policy timeout and a caller-provided cancellation event.
- Terminates the CLI process group, escalates termination if needed, and
  attempts to stop a still-running Docker container after timeout,
  cancellation, or output overflow.
- Returns bounded decoded output and execution metadata.

## Limitations and operational follow-up

This is an initial supervisor, not yet a production execution service.
Container cleanup is best-effort if the Docker daemon is unavailable or the
worker is terminated abruptly. Production deployment still needs a startup
reconciler for orphaned containers, worker-level watchdogs, signed/promoted
image provenance, structured audit-ledger integration, per-tenant quotas,
and deployment tests against the exact rootless runtime and host kernel.
Do not treat unit tests with fake processes as proof of kernel isolation.

## Tests

```bash
python -m unittest tests.test_evolution_sandbox_supervisor
```
