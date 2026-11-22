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
- `AgentExecutionLoop(max_total_tokens=N)` provides a strict cumulative
  token budget: every model turn must return exact provider usage, estimated or
  missing usage fails closed, and a turn that exceeds the cumulative limit is
  stopped before any proposed tools are dispatched or another model turn begins.
  The embedding layer must pass the execution spec's token budget into this
  parameter; the generic loop does not infer a task spec by itself.
- The specialist worker's `ExecutionBudget.max_tokens` remains a UTF-8 byte
  ceiling for its text-only handler contract. It must not be confused with
  provider token counts.
- Timed-out Python handlers cannot be forcibly terminated safely. They run in
  daemon threads, and each worker permits at most four outstanding agent calls;
  exhausted capacity fails closed. Built-in specialist handlers must remain
  side-effect-free. This is a deadline/result fence, not an OS process sandbox.
- `max_memory_mb` is declarative only at this stage. Hard memory/CPU isolation
  requires the separate isolated execution runner; in-process Python cannot
  provide that guarantee.

These limits do not grant capabilities or replace task authorization, lease
fencing, approval checks, or worker-session fencing.


### Wiring usage ledgers into authenticated conversations

The API runtime accepts either a shared `usage_tracker` for a single-agent
runtime or `usage_tracker_for_owner(owner_id)` alongside
`agent_loop_for_owner`. The owner ID is resolved by the authenticated
conversation service; the tracker factory is supplied by the embedding
application and must choose a private, owner-isolated ledger location. The
runtime caches one tracker per owner for its lifetime. Keep each agent's ledger
path distinct when multiple agents share an owner, and do not expose raw usage
files through public static routes.


## Immutable task ownership

New tasks created through `TaskRuntime.enqueue` persist the authenticated actor as
an immutable `owner_id` in the workflow row. The SQLite migration adds the nullable
column without guessing ownership for pre-existing records: legacy rows remain
unowned. Runtime records expose the persisted owner, and explicit retries preserve
the original owner. Idempotency digests include the owner for newly owned tasks,
while legacy unowned digests retain their historical form.

Owned tasks may depend only on tasks with the same owner. Cross-owner and
owned-to-legacy dependency edges are rejected transactionally. This is an
additive persistence boundary; it does not replace the API authorization policy.
The authenticated API must pass its verified principal as `actor_id`, and direct
store access remains a trusted internal interface.

The Docker sandbox refuses tasks without a persisted owner. Its operator-provided
workspace resolver must use `task.owner_id` and `task.task_id` to resolve a
private per-owner workspace, reject symlink/path escapes, and never trust a path
from task input or model output. Legacy unowned tasks cannot execute in the
sandbox until explicitly migrated through a separately authorized ownership
reconciliation process. Yuki does not infer or backfill owners from task contents.

Task detail reads, event streams, task listings, summaries, and user-facing
mutations are owner-scoped. A cross-owner or legacy-unowned task is denied unless
the caller has the separate `task:owner:override` capability for that task.
Global listings require the separate `task:owner:list:any` capability in
addition to `task:list`. These overrides are intended only for explicitly
trusted administrative or worker-service identities; they must not be granted
to ordinary users or model-generated agents. Worker deployments that update
tasks on behalf of their owners must receive the narrowly scoped override
through the trusted authorization policy. The store's unscoped methods remain
internal trusted-service interfaces and must not be exposed directly to clients.

## Private task workspace resolution

`core.task_workspace.TaskWorkspaceResolver` provides the default filesystem
boundary for sandbox profiles. Provision a dedicated absolute workspace root
owned by the Yuki service UID, with no group/world write permissions, and pass
that resolver to the operator-created sandbox profile:

```python
from core.task_workspace import TaskWorkspaceResolver

workspace_resolver = TaskWorkspaceResolver("/var/lib/yuki/workspaces")
```

The resolver derives opaque directory names from SHA-256 digests of the
persisted `owner_id` and `task_id`. It creates a separate private directory
for each task under its owner's namespace. It uses directory-relative opens
with `O_NOFOLLOW`, validates directory ownership and permissions, rejects
symlinked roots/children, and fails closed on platforms without the required
safe filesystem operations. It does not accept filesystem paths from task
definitions, prompts, or model output. Legacy unowned tasks are rejected.

The root must be provisioned before the worker starts; the resolver does not
create or repair an untrusted root. Mount only the resolved task directory into
the sandbox, never the account parent directory. Workspace cleanup and retention
are separate lifecycle operations and must not recursively delete paths based
on untrusted task input.

## Isolated Docker task execution

`core.sandbox_executor.DockerSandboxExecutor` is an explicit, opt-in execution
backend. It is not started by API imports and does not replace the existing
specialist worker. A deployment must provide an absolute, non-symlink Docker
CLI executable, an existing non-symlink rootless Docker Unix socket path, and
an operator-trusted `runtime_verifier(policy, image)`. The CLI receives only
that explicit `DOCKER_HOST` endpoint plus a minimal environment; it does not
inherit the API process's credentials, Docker context, or arbitrary environment.
The verifier must inspect the actual host daemon/runtime and return
`SandboxVerification` only after verifying the required controls, the exact
immutable image digest, the canonical policy digest, the configured AppArmor
profile, and a short evidence expiry. Docker's own `info` JSON is diagnostic
only and must never be used as the verifier by itself.

The executor rejects absent, expired, mismatched, or incomplete evidence before
launch. It applies a unique container name, retains Docker's default seccomp profile
(without allowing an unconfined override), and selects the configured AppArmor
profile, rootless/non-root execution, dropped Linux
capabilities, no-new-privileges, no network, read-only root, policy-governed
workspace mount, cgroup CPU/memory limits, swap equal to memory, PID limits,
and bounded tmpfs. It uses no shell, passes a minimal environment, bounds
combined stdout/stderr capture, enforces a monotonic wall-clock deadline, kills
the Docker client process group on timeout, and always attempts named-container
removal. If cleanup cannot be verified, the call raises
`SandboxRuntimeError`; the deployment must alert and reconcile the named
container before accepting more work.

This is a backend primitive, not yet a production attestation implementation:
Yuki does not ship a trusted host probe or an AppArmor profile installer. The
deployment must implement and protect that verifier out-of-process and install
the named AppArmor profile before enabling the backend. A verifier that merely
returns hard-coded booleans defeats the trust model. The executor also does
not yet implement an egress gateway, Windows/macOS isolation, VM isolation, or
cross-host scheduling. Allowlisted networking remains fail-closed. Do not wire
untrusted user-supplied commands into this executor until task authorization,
workspace ownership, and an approved command/image policy are integrated.


Worker completion and failure are fenced inside the same SQLite write
transaction that changes task state and appends its terminal event. The store
checks the expected task version, running state, exact lease owner, and
unexpired lease before allowing either transition. A worker cannot use a
successful preflight check to race a lease expiry and commit a stale result.

## Orphan container recovery

The executor labels managed containers with the non-secret task ID, worker ID,
a SHA-256 fingerprint of the persisted account owner, registered profile ID,
and immutable image digest. These labels are metadata for reconciliation, not
credentials or proof of isolation.

`core.sandbox_recovery.DockerOrphanRecovery` is an explicit operator-invoked
recovery utility. It enumerates only containers carrying the Yuki management
label, inspects each candidate, verifies the generated container name, full
container ID, complete label set, image reference and digest, then consults
the durable task store. It removes a container only when the injected callback
returns explicit `False` for the exact task, worker and owner fingerprint.
Active, unknown, malformed, uninspectable, or mismatched candidates are
preserved. Docker discovery or inspection errors fail closed. The utility
never invokes `docker prune` and is not started implicitly by importing the
API or worker service.

A deployment should construct the callback using
`durable_task_lease_is_active(store, task_id, worker_id, owner_sha256)`.
That check requires a persisted owner, matching owner fingerprint, running
state, exact lease owner, and unexpired lease. Keep recovery under an
operator-controlled maintenance identity and test it against the deployment's
rootless Docker daemon before enabling scheduled recovery.

## Durable sandbox lifecycle audit

Sandbox execution emits `task.sandbox.started` followed by one of
`task.sandbox.completed`, `task.sandbox.cancelled`, or
`task.sandbox.failed`. The specialist worker writes these events through the
task runtime, not directly to SQLite. Event payloads contain only the pinned
image SHA-256 digest, registered profile ID, Yuki-generated container name,
worker ID, exit code, elapsed milliseconds, captured output byte count, and
timeout/cancellation flags. Raw stdout/stderr, environment variables, secrets,
workspace contents, and command arguments are not copied into lifecycle events.

Appending a lifecycle event requires the current running task, matching worker
lease owner, unexpired lease, and expected task version in one
`BEGIN IMMEDIATE` transaction. Lease renewal and event append can race, so the
worker re-reads the version and retries a bounded number of times. Failure to
record the start event prevents launch; failure to record the outcome fails the
worker operation closed. Lifecycle events advance the task version, and the
worker reloads that version before its terminal task transition.

The separate `task:sandbox:audit` action is required. Worker-service identities
also need the narrowly scoped `task:owner:override` grant to act on behalf of
the task owner. Do not grant either capability to model-generated agents or
ordinary user sessions.

### Agent-to-sandbox dispatch contract

The specialist worker can opt into sandbox execution by receiving an explicit
`sandbox_executor` and an operator-owned `sandbox_profiles` mapping keyed by
immutable registered `spec_id`. A spec must request `sandbox.execute`, and the
worker's separately configured `handler_capabilities` must grant that
capability. The runtime authorization callback must also grant the worker
service actor `task:sandbox:execute` for the exact task.

Each `SandboxExecutionProfile` pins one immutable image digest, one validated
`SandboxPolicy`, one fixed command tuple, and a trusted
`workspace_resolver(task)`. Commands and workspace paths are not taken from
the task definition or model output. The resolver is deployment-owned and
must return a private workspace belonging to the task's authenticated owner;
it must not return a shared directory or a path derived from untrusted task
text. The worker checks the task is currently running under its own lease and
that the execution budget fits within the profile's runtime and memory caps.
Only successful bounded UTF-8 stdout is accepted as the specialist result.
Missing profiles, missing grants, failed authorization, invalid leases,
timeouts, nonzero exits, invalid text, or truncated output fail closed.

This integration is opt-in and does not make Docker available by default.
Deployments still need a trusted host verifier, rootless daemon, installed
AppArmor profile, owner-scoped workspace resolver, and an explicit approved
command profile before enabling any sandbox-capable agent.


### Sandbox cancellation and lease loss

During an authorized sandbox task, the specialist worker polls the durable task
record while the Docker client is running. Explicit task cancellation, lease
loss, task deletion, or inability to read the lease state causes the executor
to terminate its Docker client process group and then forcibly remove the
uniquely named container in its cleanup path. A cancellation result is not
converted into task success; the worker returns the already-cancelled task
record. Lease loss is fenced from completion. Cleanup errors remain hard
execution failures and require operator reconciliation. The cancellation
latency is bounded by the worker's polling interval plus Docker teardown time;
this is not a kernel-level instantaneous kill guarantee.
