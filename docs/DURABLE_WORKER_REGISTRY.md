# Durable worker registry

Yuki's automation worker registry is operational metadata, not an authorization
or scheduling system. It records one current process session per worker ID and
supports bounded health inspection.

## Lifecycle

- `register(worker_id, metadata=...)` creates a new random process-session
  token. Re-registering the same worker ID atomically replaces the old session.
- Store only the returned token in the worker process. SQLite persists its
  SHA-256 digest, never the raw token.
- `heartbeat(worker_id, session_token=...)` updates last-seen time only for
  the current session in `active` or `stopping` state.
- `set_status(..., status="stopping")` marks shutdown intent. Heartbeats remain
  valid while stopping so in-flight work can finish safely.
- `set_status(..., status="stopped")` closes the session. Stopped sessions
  cannot heartbeat or change status.
- `list_workers(stale_after_seconds=...)` derives a `stale` health state from
  last-seen time. It does not mutate stored state or claim that a stale worker
  has been safely terminated.

## Security and boundaries

- The database path uses Yuki's private SQLite path preparation. For atomic claim fencing, pass the same database path to `WorkerRegistry` and `WorkflowRunStore`.
- Worker IDs, metadata size, field count, stale threshold, and result count are
  bounded and validated.
- Session replacement and lifecycle updates use SQLite write transactions.
  A delayed process cannot overwrite the replacement process's status because
  every mutation is fenced by the session-token digest.
- Task claims verify the worker session inside the same `BEGIN IMMEDIATE` transaction that selects and leases the task. The registry and workflow store must share one SQLite database file. Claims without a registered session preserve the existing non-registry worker path.
- Registry records do not include session tokens and do not expose task
  arguments, prompts, results, credentials, or provider configuration.
- This registry does not grant task permissions or renew task
  leases, or authorize an execution. Existing task-runtime authorization and
  lease fencing remain authoritative.


## Authenticated health API

Pass a `WorkerRegistry` to `build_conversation_runtime(..., worker_registry=registry)`
to expose `GET /v1/admin/workers`. The endpoint requires a valid Yuki bearer
session and the administrator role. It returns at most 200 sanitized operational
records, derives stale status using a 60-second heartbeat threshold, and never
returns session tokens or session digests. Other routes continue to the wrapped
API. The registry must use the same SQLite database file as `WorkflowRunStore`
when it is also used for atomic task-claim fencing.

Metadata should contain only non-sensitive operational labels (for example,
worker role and build version); never store credentials, prompts, user content,
or secrets in it.

## Parallel workers and supervision

Use `RegisteredSpecialistWorker.run_concurrently()` for a registered worker
pool. Each child receives its own registry session before it can claim tasks;
each child also revalidates the parent session immediately before every claim
and independently refreshes its registry heartbeat while active. A child stops
claiming new work if its registry heartbeat fails, and its session is retired
when it exits. Calling the underlying
`SpecialistTaskWorker.run_concurrently()` directly from a registered worker
without child-session callbacks fails closed rather than allowing unregistered
parallel claims.

`SpecialistWorkerSupervisor` is an explicit lifecycle owner for up to 64
registered workers. The packaged `yuki-workers` command loads an operator-trusted
factory configured with `YUKI_WORKER_SUPERVISOR_FACTORY=module:callable`.
The factory must construct workers with an authorized service actor, approved
handlers, and a shared workflow/registry database, then return a supervisor.
The supervisor optionally accepts an explicit `recovery_callback` and bounded
`recovery_interval_seconds`. Wire this only to an authorized
`TaskRuntime.recover_expired(actor_id=...)` call using the deployment's
pre-authorized service actor. The callback runs at startup and then periodically;
it must return the list/tuple of recovered task records. Recovery only marks
expired leases `interrupted` and journals the transition. It does not replay or
requeue work, and it does not infer task expiry from a stale worker heartbeat.
A recovery callback failure stops supervisor startup/operation and closes
registered sessions rather than silently claiming recovery succeeded.

The launcher handles SIGINT/SIGTERM by requesting graceful shutdown; the
supervisor stops new claims, allows active synchronous handlers to finish, and
closes worker sessions. Importing the service module starts no background work.
The web application does not start specialist workers implicitly. The factory
module must be installed in the deployment environment and treated as trusted
executable configuration.
