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



## Declarative agent execution specifications

`automation.agent_execution_spec.AgentExecutionSpec` provides an additive,
version-1 contract for describing an agent's role, bounded instructions,
requested capability labels, dependencies, input contract, output types,
approval policy, retry policy, and execution budget. Specs are immutable
dataclasses with strict field/type/size validation and JSON-compatible
`to_dict()` / `from_dict()` serialization. The dependency graph validator
rejects unknown IDs and cycles and returns a deterministic topological order.

Task creation and its initial `task.created` audit event now share the
same SQLite transaction as the optional agent-spec binding. If the audit insert
fails, the task and binding roll back together. An idempotent retry repairs a
missing initial event without creating a duplicate task.

Tasks may now be bound to an execution spec at enqueue time with
`TaskRuntime.enqueue(..., agent_execution_spec_id=...)`. The workflow store
validates the spec and role, captures its digest, and creates the task binding
inside the same SQLite transaction as the task row. The spec ID is included in
the idempotency digest, so a retry cannot silently switch agents. Existing
unbound-task idempotency digests remain unchanged.

At claim time, `required` and `per_action` approval policies keep the task
unclaimable until the existing session-bound approval is recorded;
`not_requested` does not add an approval requirement. Before invoking a
handler, the worker verifies the persisted spec digest, role, and supported
text output contract. Registry reads and approval eligibility also verify the
canonical spec digest, so tampering cannot silently weaken an approval policy.
Missing or altered specs fail closed. Declared capability
labels remain descriptive and do not grant runtime permissions. Execution
budgets are not yet enforced by this worker and must not be treated as OS or
provider resource limits.

Execution specs are persisted in the same private SQLite database as task
records. `WorkflowRunStore.register_agent_execution_spec()` stores a canonical
JSON representation and SHA-256 digest. Re-registering an identical spec is
idempotent; changing the contents under an existing spec ID is rejected. A
changed configuration must use a new ID, preserving immutable audit references.
`TaskRuntime.register_agent_specs()` validates and atomically registers a complete
bounded dependency graph in deterministic topological order. A conflicting
immutable ID rolls back the entire batch; no partial graph is left behind.
`TaskRuntime.register_agent_spec()`, `register_agent_specs()`, `get_agent_spec()`, and
`list_agent_specs()` enforce separate `task:spec:register`,
`task:spec:read`, and `task:spec:list` authorization actions. There is no
unauthenticated registry path through the task runtime.

A spec is descriptive configuration, not an authorization grant or executable
plan. Capability labels must be checked against the authenticated task actor
and deployment policy at dispatch time. The runtime must enforce resource
budgets, approval requirements, cancellation, timeouts, and output contracts;
declaring a budget does not itself impose an operating-system limit. The default
retry policy is `manual_only`; no spec can cause automatic replay of an
interrupted task. The default approval policy is `required`. This contract
does not add a scheduler, provider selection, shell access, or implicit network
access, and it does not change existing workflow persistence.

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


### Agent capability enforcement

SpecialistTaskWorker accepts an explicit handler_capabilities allowlist for each registered handler. Before invoking a handler for a spec-bound task, the worker verifies that every requested capability is present in that handler's trusted grant set. Missing grants, unknown handler grants, and malformed grant configuration fail closed. Capability labels in an agent spec are requests, not permissions; they never grant access by themselves. This check is a dispatch-time boundary and does not replace authorization inside individual tools or external services.


### Durable task dependencies

Tasks may be enqueued with up to 32 explicit prerequisite task IDs. Dependency edges
are inserted in the same SQLite transaction as the new task and its creation event.
The runtime requires the caller's dedicated `task:dependency` authorization for
each referenced task. Idempotency digests include dependency IDs while preserving
legacy digests for tasks without dependencies.

Workers only claim a task after every prerequisite reaches `succeeded`. A
retryable `failed` prerequisite keeps dependants queued; a `cancelled`,
`interrupted`, or already `blocked` prerequisite causes dependants to transition
to terminal `blocked` with a `task.blocked` audit event. Dependency checks and
claims are serialized in the workflow-store transaction. This is a task-level
dependency mechanism; agent-spec dependencies remain a separate declarative
specification graph.


### Retry lineage and dependency recovery

Explicit retries are recorded in `workflow_run_retries` in the same transaction
that creates the retry task and its `task.created` provenance event. Dependency
readiness follows the retry lineage recursively: a prerequisite is satisfied when
the original task or any explicitly linked retry succeeds. A dependent remains
queued while any attempt in that lineage is still eligible for an explicit retry;
it is blocked only when every attempt is cancelled or blocked. Interrupted and
failed attempts remain retryable, and retries still require fresh approval where
the task contract requires it.


### Agent execution-budget enforcement

The specialist worker enforces the immutable execution spec before and during
handler dispatch:

- `max_steps` is checked against the persisted task definition before invoking
  a handler.
- `max_runtime_seconds` is enforced as a wall-clock handler deadline. On expiry,
  the task is failed with `agent_runtime_budget_exceeded`; any late handler
  result is discarded and cannot complete the terminal task.
- `max_tokens` is currently enforced as a conservative UTF-8 text-byte ceiling
  for handler input and output, not as exact model-token accounting.
- Ollama and OpenAI-compatible non-streaming adapters now expose exact input and
  output token counts when the provider supplies valid usage metadata. Missing
  usage is not guessed; malformed usage is rejected.
- `AgentExecutionLoop(on_usage=...)` forwards validated usage records. Connect
  `TokenUsageTracker.record_provider_usage` as the callback and give each agent
  an isolated ledger path plus task/session ID to persist per-agent metrics.
  Provider-reported counts are marked exact; estimates remain explicitly marked.
- Streaming responses only contribute usage when the provider includes usage in
  its final response event. No count is synthesized from streamed text.
- Timed-out Python handlers cannot be forcibly terminated safely. They run in
  daemon threads, and each worker permits at most four outstanding agent calls;
  exhausted capacity fails closed. Built-in specialist handlers must remain
  side-effect-free. This is a deadline/result fence, not an OS process sandbox.
- `max_memory_mb` is declarative only at this stage. Hard memory/CPU isolation
  requires the separate isolated execution runner; in-process Python cannot
  provide that guarantee.

These limits do not grant capabilities or replace task authorization, lease
fencing, approval checks, or worker-session fencing.
