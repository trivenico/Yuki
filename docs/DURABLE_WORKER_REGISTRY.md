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

- The database path uses Yuki's private SQLite path preparation.
- Worker IDs, metadata size, field count, stale threshold, and result count are
  bounded and validated.
- Session replacement and lifecycle updates use SQLite write transactions.
  A delayed process cannot overwrite the replacement process's status because
  every mutation is fenced by the session-token digest.
- Registry records do not include session tokens and do not expose task
  arguments, prompts, results, credentials, or provider configuration.
- This registry does not grant task permissions, claim tasks, renew task
  leases, or authorize an execution. Existing task-runtime authorization and
  lease fencing remain authoritative.

Metadata should contain only non-sensitive operational labels (for example,
worker role and build version); never store credentials, prompts, user content,
or secrets in it.
