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

- Sandbox execution tasks are terminally failed when their worker lease expires, regardless of the configured retry count. Recovery also emits `task.outcome_unknown`, making the task visible in the operator reconciliation queue; it is never automatically replayed because the previous worker may have completed an external side effect before losing its lease.
- Inspect the durable task state and hash-chained audit events before deciding whether to submit a new task.
- An `outcome_unknown` event requires manual reconciliation. Do not blindly retry the command.
- The current worker is serial. Do not scale replicas until concurrency limits, lease behavior, host isolation, and operational monitoring have been reviewed.
- The worker records a durable process-presence heartbeat in the task database. Operators can inspect active and stale worker records through the engine's `list_workers(stale_after_seconds=...)` interface; this is not exposed as an unauthenticated network endpoint. A crashed process remains `active` in storage but is marked `stale` by the query after its heartbeat deadline. A graceful shutdown marks it `stopped`.

## Operator reconciliation endpoints

Administrators with the users:manage permission can inspect:

- GET /v1/sandbox/operator/unknown-outcomes — metadata-only list of failed sandbox tasks carrying a durable task.outcome_unknown event
- GET /v1/sandbox/operator/workers — worker process-presence records, including the derived stale flag (30-second threshold)

These endpoints require an authenticated administrator session. They do not return task payloads, command arguments, environment variables, approval tokens, or credentials. The unknown-outcome list is read-only: it does not convert a failed task to success or authorize a retry.

For each unknown outcome, an operator should correlate the task ID and fingerprint from the protected audit log with the external system's authoritative state. Record the investigation and evidence in the incident/change-management system. Only submit a replacement task after independently establishing that the original side effect did not occur or that repeating it is safe. Do not alter durable task state manually.

## Validation boundary

The presence of this entrypoint does not mean sandbox execution is enabled or production-ready. Keep the feature disabled until the deployment-specific host, Docker, identity, storage, network, and incident-response controls have been tested.

## Submission quotas

The authenticated sandbox API currently applies persistent per-user limits in the durable task database:

- Goal creation: 10 requests per rolling 60-second window
- Task approvals: 20 requests per rolling 60-minute window
- New task submissions: 10 requests per rolling 60-second window
- Active sandbox tasks: at most 5 queued or running tasks across all goals owned by one user

Exact idempotent retries return the original durable task and do not consume another submission slot. Submission idempotency receipt creation, the per-user submission rate-limit event, and task insertion are committed in the same SQLite transaction, including concurrent duplicate requests. Rate-limit events store only the actor ID, action name, and timestamp; they do not store command arguments or credentials. Exceeding a request or active-task quota returns HTTP 429. These are conservative code defaults in this release, not yet operator-configurable policy. Monitor rejected requests before considering any increase.

