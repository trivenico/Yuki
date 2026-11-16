# Sandbox execution operations

Yuki's sandbox submission API and sandbox executor are separate processes.

- The web/API process authenticates users, records approvals, and queues durable tasks.
- The worker process claims only `sandbox.execution.v1` tasks and dispatches them through the configured Docker sandbox.
- The web process does **not** start the worker. Run the worker as a separately supervised service.

## Preconditions

Do not enable sandbox execution until all of the following are true:

1. `sandbox_execution_enabled` is explicitly set to `true`.
2. `sandbox_execution_allowed_images` contains only reviewed, digest-pinned images (`name@sha256:<64 lowercase hex characters>`). Do not use mutable tags.
3. The web process and worker use the same configured workspace and therefore the same `auth.sqlite3` database.
4. The Docker daemon is dedicated to Yuki and is not shared with untrusted workloads. Prefer a dedicated unprivileged service account and a rootless Docker daemon. Access to a rootful Docker socket is effectively host-administrator access.
5. The worker's OS account can write only the required Yuki state/workspace directories and access only the intended Docker endpoint. Do not place provider credentials, cloud credentials, or unrelated user files in the sandbox workspace.
6. The deployment has TLS and authentication at the web boundary, durable backups for task/auth/audit databases, monitoring, and an operator procedure for reconciling unknown execution outcomes.

The container flags in the sandbox runtime are defense in depth, not a substitute for isolating the Docker daemon and host.

## Start the worker

From the repository root, using the same Python environment and configuration as the web process:

```bash
python -m scripts.run_sandbox_worker
```

The worker reads Yuki configuration, opens the same `<workspace>/auth.sqlite3` used by `scripts.run_web`, and exits if sandbox execution is not explicitly enabled and correctly configured.

Set `sandbox_execution_poll_interval_seconds` to a number from 0.1 through 30 to tune idle polling; the default is 1 second. The worker handles SIGINT/SIGTERM and stops between task executions. Configure the process supervisor to restart unexpected exits and allow enough shutdown time for the maximum sandbox command timeout plus container cleanup.

## Recovery and incident handling

- Each sandbox task has one execution attempt. Expired leases are terminally failed rather than automatically replayed because a previous worker may have completed an external side effect before losing its lease.
- Inspect the durable task state and hash-chained audit events before deciding whether to submit a new task.
- An `outcome_unknown` event requires manual reconciliation. Do not blindly retry the command.
- The current worker is serial. Do not scale replicas until concurrency limits, lease behavior, host isolation, and operational monitoring have been reviewed.
- This worker does not provide per-user quotas, API rate limiting, or a network health endpoint. Those are outstanding production-hardening tasks.

## Validation boundary

The presence of this entrypoint does not mean sandbox execution is enabled or production-ready. Keep the feature disabled until the deployment-specific host, Docker, identity, storage, network, and incident-response controls have been tested.
