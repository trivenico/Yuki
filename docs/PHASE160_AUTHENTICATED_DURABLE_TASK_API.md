# Phase 160 — Authenticated durable task API

## Included

The authenticated ASGI application can expose durable goal/task workflows when a
`DurableTaskEngine` is injected into `build_conversation_runtime`.

The HTTP layer does not accept a caller-supplied owner ID. It resolves the
bearer session and passes the opaque session token to
`AuthenticatedDurableTaskService`, which independently resolves the current
principal before every operation. Cross-owner reads and mutations return the
same generic not-found response.

Routes:

| Method | Route | Purpose |
|---|---|---|
| POST | `/v1/goals` | Create a goal owned by the authenticated user |
| GET | `/v1/goals/{goal_id}` | Read an owned goal |
| POST | `/v1/goals/{goal_id}/tasks` | Submit a task to an owned goal |
| GET | `/v1/goals/{goal_id}/tasks` | List tasks in an owned goal |
| GET | `/v1/goals/{goal_id}/events` | Read bounded event history |
| GET | `/v1/tasks/{task_id}` | Read an owned task |
| POST | `/v1/tasks/{task_id}/cancel` | Cancel an owned queued task |

Task submission accepts only `task_type`, object-valued `payload`,
`idempotency_key`, and optional `dependencies`, `max_attempts`, and
`delay_seconds`. The service writes the owner from the authenticated session
to the task metadata. Payload identity claims are not authorization data.

## Local web deployment

The standard `scripts/run_web.py` launcher creates
`durable_tasks.sqlite3` in the configured workspace and injects the
engine into the ASGI runtime. The runtime owns the engine and closes it during
graceful shutdown. The durable task engine uses SQLite and is intended for a
local filesystem; it is not a distributed queue.

## Security and operational boundaries

- Authentication is required for all goal/task routes.
- Session validity is checked at the service boundary on every operation.
- Ownerless legacy goals cannot be claimed by a session.
- Cross-owner resources return `404 not_found`.
- Request bodies are bounded by the API's existing body limit.
- Cancellation is safe only for queued tasks; running tasks cannot be forcibly
  interrupted through this endpoint.
- These endpoints submit and inspect durable work. They do not expose a public
  arbitrary-code execution endpoint or bypass the registered-skill worker
  policy and approval boundary.
- The local launcher remains a personal-use entry point, not an internet-facing
  deployment. Use TLS, trusted-host validation, rate limits, secret management,
  and network controls before remote exposure.

## Validation

The API and runtime test suites cover authenticated goal/task lifecycle,
owner isolation, invalid request fields, and task-engine shutdown. Exact-head
CI is required before integration.
