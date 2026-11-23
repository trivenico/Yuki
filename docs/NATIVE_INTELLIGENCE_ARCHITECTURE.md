# Native Intelligence Architecture

## Decision

Yuki is the product and owns its application intelligence. External model providers are optional adapters, not the system of record, security boundary, task authority, or required foundation for local product workflows.

This is an architectural direction, not a claim that Yuki already contains a trained foundation model or human-level autonomous reasoning engine.

## Day-one intelligence without training from scratch

Yuki does not need a project-specific foundation-model training run before its first useful release. The product should ship with deterministic native capabilities—intent routing, planning, retrieval, validation, memory and guarded tool use—that work immediately and require no model service.

For broad natural-language generation and open-ended reasoning, there are only two honest options:
- Run pre-trained model weights locally. This avoids a hosted AI dependency and avoids training Yuki from scratch, but the weights are still a pre-trained model asset that must be selected, licensed, distributed or downloaded, and hardware-qualified.
- Train or distill a Yuki foundation model. This creates model weights owned by Yuki, but it is a separate research, data-governance, compute and evaluation programme; it cannot be promised as an effortless no-training feature.

The initial product path is local-first and model-optional: native deterministic intelligence remains available without weights, and an explicitly installed local model upgrades language capability without handing control to a third-party service. No cloud provider is required for startup, memory, planning, task state, permissions or execution.

The native router must abstain when capability matches are ambiguous. It may suggest registered capabilities, but the normal approval and guarded execution boundary remains mandatory.

## Implemented native baseline

The first provider-independent intent layer is implemented in
`core/native_intelligence.py`. It normalizes Unicode, expands a bounded
synonym vocabulary, ranks registered skill descriptions/tags/examples, and
returns an explicit no-match or ambiguous result instead of guessing.
`YukiCoreAgent` uses that resolution before entering its existing permission
gate and guarded skill runtime. Intent matching is discovery only: it cannot
invoke a skill or authorize a side effect.

This baseline is deterministic capability routing, not general semantic
reasoning. The terminal `YukiAgent` now consults the native capability router
before any optional provider. The CLI supplies an interactive approval callback
for the default read-only time, system and workspace-listing skills. A denied
approval or ambiguous match is returned directly and cannot be bypassed by
falling through to an optional model provider.

Explicit planning requests also use Yuki's bounded native planner. It emits a
dependency-checked task proposal and execution checklist without executing
anything. The CLI grounds plans in locally saved lessons; retrieved entries are
displayed as reference material, never treated as instructions or policy.

This is a foundation to extend with local retrieval, context-aware planning,
memory grounding, and evaluation—not a claim that those later capabilities are
already complete. The optional `llama_cpp` provider now runs GGUF weights
in-process through the native C++ llama.cpp runtime, without an Ollama server
or hosted inference API. It supplies text generation only; native Yuki routing,
planning, approvals and guarded execution remain authoritative. See
[Local inference runtime](LOCAL_INFERENCE_RUNTIME.md).

## Ownership boundaries

- **Yuki core:** identity, conversation persistence, memory, deterministic planning, capability discovery, permissions, approvals, task state, audit, recovery, and user-facing workflows.
- **Native intelligence:** deterministic intent handling, policy-aware routing, retrieval over user-approved local material, structured planning, state machines, validation, and evidence-based synthesis. These capabilities must remain useful without a remote model.
- **Optional model adapters:** the in-process llama.cpp runtime, Ollama, OpenAI, Gemini and future backends may provide language generation or model-assisted decomposition. The local llama.cpp adapter does not require a server or hosted API; it does require a separately supplied pretrained GGUF model. They must implement narrow contracts and may not own permissions, persistence, or side-effect execution.
- **Execution boundary:** model output is untrusted input. Only Yuki's guarded tool/runtime layer may authorize and execute registered actions.

## Degraded operation

Yuki must start and expose its local/core workflows when no model is configured, credentials are absent, or a provider is unavailable. Model-dependent free-form generation may be reported as unavailable, but local commands, memory, task inspection, deterministic planning, approvals, and other independently implemented core features must not be disabled as a side effect.

No implicit cloud fallback is permitted. Provider selection and cloud routing remain explicit. A provider's configured/installed status must not be described as verified inference readiness unless the relevant check has actually run.

## Build sequence

1. Stabilize provider-independent application services and make startup independent of external model credentials.
2. Define and test deterministic native intent, planning, retrieval, and workflow contracts.
3. Keep model adapters behind the same narrow interfaces; test core workflows with providers absent.
4. Add optional local inference and optional remote adapters as interchangeable enhancements.
5. Evaluate native capabilities against fixed, versioned task suites before describing them as intelligent, autonomous, or production-ready.
6. Keep every side effect behind existing authorization, approval, audit, and recovery controls regardless of which model proposed it.

## Non-goals and claims discipline

- This decision does not claim that Yuki has trained its own foundation model.
- A deterministic planner or rules engine is not a substitute for general language understanding.
- A provider adapter is not an agent and cannot grant itself capabilities.
- A catalog entry or specialist profile is not a running model instance.
- Model unavailability must not be hidden by returning fabricated or mock-generated answers in production.

## Acceptance criteria

- A clean installation can launch Yuki's core interface without an API key or model download.
- Provider-independent commands and local workflows pass tests with all model adapters absent.
- Missing-provider behavior is explicit, safe, and does not silently substitute a test mock.
- Optional adapters can be enabled, selected, tested, and removed without changing core data formats or permission policy.
- Provider failures do not corrupt conversation/task state or bypass cancellation, audit, or recovery.

## Consent-gated conversation memory

The authenticated conversation runtime accepts an optional `memory_for_owner(owner_id)`
composition callback. The callback must return a memory facade scoped to that
authenticated owner and expose:

- `context_enabled`: an explicit boolean consent state.
- `context_entries(query=..., limit=...)`: a bounded list of saved notes.

When consent is disabled, retrieval is not called and no memory is added to the
model context. When enabled, Yuki validates and bounds the returned entries,
labels them as untrusted reference data, and appends them to the system context.
A retrieval or validation failure prevents the model request. Memory is never
interpreted as policy, permissions, tool instructions, or approval.

The runtime does not create a shared global memory store or infer consent.
Deployments must inject an owner-isolated memory facade; leaving the callback
unset preserves the existing no-memory behavior.

## Operational awareness and human-centered communication

`core/awareness.py` represents the current interaction mode, goal, known
application context, open questions, recent events, and uncertainty in a bounded
snapshot. Conversation turns receive a foreground snapshot; applications may
inject an authenticated owner-scoped awareness resolver to include verified
background state. Every item is treated as untrusted reference context and
cannot grant permission or override policy. This is operational context
tracking, not consciousness or human emotion.

`core/humanizer.py` adds response guidance for natural, respectful language,
clear uncertainty, correction handling, and user-voice preservation. It does
not claim feelings, personal experience, or human identity, and it cannot
change tool access or safety rules.

## Foreground and background learning

`core/learning.py` separates observation from durable learning. Explicit
foreground feedback and verified background task outcomes can create bounded
review candidates. Candidates are isolated by owner. Yuki saves a lesson to
that owner's memory only after learning consent is enabled and the owner
explicitly approves the candidate; consent is checked again at commit time.
Unverified outcomes are rejected, and declining a candidate discards it.

The review queue can use `LearningQueueStore`, a SQLite-backed,
owner-isolated store with atomic review claims, bounded pending items, and
stale-review recovery. The coordinator can be recreated after process restart
without losing pending candidates. Approved memory writes use the candidate
ID as an idempotency key so a retry after a crash does not duplicate the lesson.
The registered specialist worker exposes an optional post-completion learning
observer for sequential and pooled background execution. It forwards only task
records in the completed state; observer failures do not change committed task
outcomes and are surfaced as a sanitized worker-health signal. The observer
must resolve the task owner, consent, and memory backend itself. Foreground
feedback is exposed through the coordinator's explicit feedback method for an
authenticated application surface. Scheduled consolidation and richer semantic contradiction detection remain follow-on work.
This mechanism stores reviewed lessons; it does not train or modify model
weights. Model self-improvement must remain separate from permission and
security policy.

The authenticated conversation API can expose foreground learning through an
injected `learning_for_user` resolver:
- `GET /v1/learning/candidates` lists only the authenticated owner's pending proposals.
- `POST /v1/learning/feedback` accepts an explicit lesson and evidence, subject to learning consent.
- `POST /v1/learning/candidates/{candidate_id}/review` records an explicit approve or dismiss decision.

The embedding application remains responsible for constructing a durable
`LearningQueueStore`, owner-scoped memory resolver, and consent resolver. The
API never accepts an owner ID from the request body. Without the resolver, the
learning endpoints remain unavailable. Duplicate checks run before proposal
creation and are repeated inside the SQLite `BEGIN IMMEDIATE` insertion
transaction, so concurrent submissions for the same owner cannot race identical
or near-identical pending lessons into the durable queue. Cross-platform CI
covers the learning queue and authenticated API contract.

The companion memory store now provides bounded deterministic lexical
retrieval. It returns active entries only, ranks by query overlap and reviewed
confidence, and records usage counters in the same locked write that returns
the selected entries. The authenticated companion memory facade uses this
retrieval only when session-scoped context consent is enabled; archived entries
are excluded. Encrypted memory inherits the same retrieval and lifecycle
behavior without exposing plaintext on disk. This is a transparent local
baseline, not semantic/vector retrieval, and saved memories remain untrusted
reference material rather than instructions or policy.


## Measured continual-learning quality

`core/learning_evaluation.py` adds a deterministic offline evaluation harness for
owner-curated retrieval cases. A caller supplies a frozen, side-effect-free,
owner-scoped retriever that returns memory IDs; the evaluator reports hit
rate@k, precision@k, recall@k, and mean reciprocal rank. Reports contain only
aggregate metrics and the case count, not queries, lesson text, or owner IDs.
The harness bounds the case set and result count, rejects duplicate IDs, and
does not mutate memory or model weights. It must be run against a frozen
evaluation snapshot rather than `MemoryStore.retrieve()`, because normal
retrieval intentionally increments usage counters. This provides a measurable
regression baseline for improving native retrieval from day one without a
separate GPU training run. It is evaluation, not model-weight learning; any
future fine-tuning or distillation remains an optional, separately governed
programme with its own datasets, consent, hardware and safety evaluations.

## Read-only memory consolidation

`core/memory_consolidation.py` provides a bounded, deterministic analysis pass
for one authenticated owner's memory snapshot. It identifies high lexical
similarity pairs and emits version-fenced suggestions for a human to inspect.
The suggested keeper is only a deterministic hint based on reviewed confidence,
usage and timestamp metadata; it is not a truth judgment. Archived memories are
excluded. 
The authenticated conversation API now exposes this review flow through
`GET /v1/learning/consolidation` and
`POST /v1/learning/consolidation/review`. Both routes resolve the coordinator
from the authenticated session; no owner ID is accepted from the request.
Consolidation is gated by the owner's learning consent. The review request must
carry both memory IDs and their observed versions; the coordinator recomputes
the suggestion and rejects stale proposals before applying the archive lifecycle
transition. Only the explicitly approved duplicate is archived; the retained
memory is never rewritten. Deployments must provide a distinct owner-scoped
memory resolver for every account.

The consolidator does not merge text, infer semantic contradictions,
change lifecycle state, delete records, or train model weights. Any future
review surface must display both memories and require an explicit owner choice
before applying a version-checked lifecycle operation. This keeps background
maintenance useful without silently rewriting personal memory.


## Proactive operational awareness

`core/proactive_awareness.py` combines bounded task summaries and worker
health records into an inspectable background-awareness snapshot. The service
uses `TaskRuntime.list_task_summaries`, which independently enforces the
caller's task-list capability and owner scope, and requires a separate
worker-health authorization callback before reading the global worker registry.
It copies only task IDs, task names, lifecycle states, lease expiry, worker IDs,
and worker status; task definitions/results and worker metadata are excluded.
Expired running leases, failed/interrupted/blocked tasks, and stale/stopped
workers become attention suggestions, never automatic retries or actions.
This is a read-only aggregation primitive; scheduling, notifications, and
action execution remain separate authorized services.


## Persistent proactive monitoring

`core/proactive_monitor.py` provides an explicit one-shot bridge from the
authorized awareness snapshot to `ProactiveAlertStore`. A host scheduler may
invoke `run_once()`; the service creates no background thread, timer, or
competing worker supervisor. It checks the actor's notification preference,
reuses the task and worker authorization boundary, and records only bounded
alert metadata.

`core/proactive_alerts.py` persists owner-isolated alert history and a
transactional notification outbox in SQLite. Stable category/subject
fingerprints deduplicate observations, cooldowns govern new notification
events, and occurrence counts survive process restarts. An outbox row is
created in the same transaction as the alert observation. Claims use
short-lived leases and hashed random fencing tokens; stale claims can be
recovered, and only the current lease can settle an event. Delivery outcomes
record status, bounded attempt counts, and exception class only—never raw
transport errors or credentials. Terminal history is bounded per owner;
pending work is retained.

`core/proactive_notification_dispatcher.py` dispatches at most one event per
explicit host invocation through an injected transport callback. Delivery is
at-least-once across crashes: a transport may have accepted a message before
the process dies, so a recovered lease can result in a duplicate. Integrations
should pass the stable notification ID as their idempotency key where the
transport supports it. No transport or scheduler is enabled implicitly.

`core/proactive_preferences.py` stores owner-scoped notification settings,
disabled by default. Authenticated `GET /v1/proactive/preferences` and
`PUT /v1/proactive/preferences` expose only the caller's setting; owner IDs
come from the authenticated principal, never the request body. The embedding
application must inject the authenticated preference store into the API and
wire its getter into the monitoring service.

The dispatcher checks the owner preference before claiming and again immediately
before invoking the transport; if consent is revoked between those checks, the
claim is released without consuming an attempt. The monitor and dispatcher
never retry tasks, change task state, grant permissions, or execute actions. Notification delivery is independent of
task execution authority. The alert history is bounded to 500 alert
identities per owner, and terminal outbox history is bounded to 1,000 records
per owner.


## Proactive dashboard integration

The Textual dashboard accepts an owner-scoped alert store, notification
preferences store, and explicit dispatcher from its authenticated host
composition. `/alerts` shows recent alert history; `/notifications` shows
the current preference and delivery history; `/notifications on|off`
updates only the authenticated actor's preference; and
`/notify dispatch CONFIRM` dispatches at most one eligible event through
the injected transport. No delivery occurs just by opening the dashboard.
The standalone VS Code launcher remains intentionally unconfigured because
it has no authenticated actor context.

The authenticated API runtime builder accepts a
`proactive_preferences_for_user` resolver and passes it into the
conversation API. The embedding application supplies the owner-scoped store;
the API derives owner identity from the authenticated session. Dashboard and
API composition therefore share explicit dependency injection rather than
creating implicit global stores or identities.


## Durable task-event awareness

`ProactiveTaskEventMonitor` reads bounded, owner-authorized task summaries
and their durable event journals. It converts only the allowlisted terminal
events `task.failed`, `task.blocked`, and `task.interrupted` into proactive
alerts. Progress, completion payloads, task results, and arbitrary event
payloads are not copied into notifications. Alert subjects include the task
ID and event sequence, making repeated scans idempotent at the alert
fingerprint layer.

The monitor is deliberately one-shot and does not own a timer or worker
thread. An authenticated dashboard host can inject it and invoke
`/monitor events`; a future host scheduler may call `run_once(actor_id=...)`
under the same actor authorization and notification-preference resolver.
The scan is bounded to 100 tasks and 100 events per task by default.
Notifications remain disabled unless the owner's preference is enabled, and
the monitor never retries or changes task state.

## Proactive operational alert lifecycle

Proactive alerts are owner-scoped operational records, not autonomous task controls.
Each alert has an immutable fingerprint, a lifecycle status (active,
acknowledged, or resolved), and a monotonically increasing version.
Acknowledgement and resolution require the authenticated owner, the current
version, and an explicit API or dashboard action. Every transition is written
to the alert audit table in the same SQLite transaction as the state change.
Stale versions and invalid transitions fail closed. Re-observing an alert
updates its occurrence metadata but does not silently reopen a resolved alert.

Authenticated API surfaces:
- GET /v1/proactive/alerts
- POST /v1/proactive/alerts/{fingerprint}/acknowledge
- POST /v1/proactive/alerts/{fingerprint}/resolve

The transition body contains only expected_version; the owner and actor are
derived from the authenticated session. The Textual dashboard supports
/alerts acknowledge ALERT_ID VERSION ACKNOWLEDGE and
/alerts resolve ALERT_ID VERSION RESOLVE. Viewing alerts remains read-only.
The alert store migration adds lifecycle columns and an audit table without
restructuring or deleting existing alert records.

## Operational Intelligence Center

The optional read-only Operational Intelligence Center aggregates bounded
metadata for the supervision dashboard. It reports owner-authorized task counts,
worker health only when the injected operator-access resolver returns exactly
true, owner-scoped alert lifecycle counts, and running tasks whose lease expiry
is already in the past. It does not read task definitions, results, event
payloads, credentials, or worker session tokens.

Use /overview (or /status) in a dashboard session where the host has injected
an OperationalIntelligenceCenter. Task listing requires task:list. Worker
registry access is a separate operator-level gate because the registry is
global operational metadata rather than owner-scoped task data. Denied or
unavailable sections are reported as unavailable instead of exposing data.
The snapshot is computed on demand; it starts no scheduler, worker, retry,
notification, or task transition.


## Deterministic daily and weekly operational summaries

`core/operational_summaries.py` provides a pure summary builder for
host-authorized task snapshots and bounded event streams. Daily summaries
cover the preceding 24 hours; weekly summaries cover the preceding seven
days, both in UTC. It counts task states and only allowlisted failure,
blocked, interrupted, and completion event kinds. It never reads task
results, definitions, or event payloads. Input consumption is bounded to
501 task records and 5,001 events so oversized or unbounded iterables fail
closed rather than being fully materialized.

Attention items are deterministic review prompts derived from observed
counts—not causal diagnoses or autonomous recommendations. The builder is
on-demand and creates no scheduler, background process, notification, retry,
or task transition. The embedding host remains responsible for obtaining
authorized records and presenting the returned summary.


## Recurring operational signals

`core/recurring_operational_signals.py` compares adjacent UTC windows and
reports repeated failed, blocked, or interrupted event categories when a
configurable count threshold is met. It uses only the allowlisted event kind
and timestamp; task IDs, payloads, results, exception text, and definitions
are not inspected. The current window is compared with the immediately
preceding window of equal length, and the report labels counts as increasing,
decreasing, or unchanged.

This is a frequency signal, not root-cause analysis. It does not assert that
events share a cause, identify a faulty component, or recommend automatic
remediation. Inputs are capped at 10,000 records, and the detector is a pure
on-demand function with no scheduler or state mutation.


## Explicit project progress intelligence

`core/project_intelligence.py` provides a pure, bounded project progress
aggregator over task membership explicitly assembled by an authorized host.
The repository's workflow task records do not themselves establish project
membership, so callers must supply the project ID and owner-consistent task
snapshot; the aggregator never guesses membership or creates a project store.

A task counts toward measured completion only when its workflow state is
`succeeded` and the host supplies a non-empty verification reference. A
succeeded task without that reference is reported separately as
`succeeded_unverified`; it does not increase the measured progress
percentage. The reference is evidence metadata, not independently validated
by this pure aggregation layer.

Dependency bottlenecks are reported only from explicit dependency IDs in the
input snapshot. Missing dependencies and dependencies not in `succeeded`
state remain unresolved. Age-based stale activity uses the task update
timestamp and a configurable 1–90 day threshold; it is explicitly a heuristic,
not proof that a milestone is stalled. No milestone deadlines are inferred.
Recommendations are review prompts with evidence task IDs, never dispatched
or executed automatically.

The snapshot is capped at 500 tasks and checks owner consistency. It reads no
task result, definition, event payload, credential, or worker token. An
embedding host must authorize project membership and obtain the task metadata
before invoking the aggregator.


## Durable owner-scoped projects and milestones

`core/project_registry.py` adds an additive SQLite registry for projects,
explicit task membership, and milestones. Project names are unique per owner.
Every project and milestone read/write requires the authenticated owner ID;
unknown and cross-owner project IDs are returned as not found.

Task membership requires an injected `task_owner_resolver`. The resolver must
look up a task through the existing owner-scoped task access boundary and
return its immutable owner ID. Membership is rejected unless that owner
matches the authenticated owner. The registry does not accept a caller-
supplied owner assertion for the task, and does not infer project membership.

Milestones support open and completed states. Completion requires a non-empty
verification reference and an expected version, so stale or repeated
completion attempts fail closed. Due timestamps must include a timezone and
are normalized to UTC. This registry does not claim that a verification
reference has been independently validated; the embedding host must attach
trusted evidence from its CI, review, or completion workflow.

The registry remains a separate injected component: existing workflow tables
and their layout are not restructured. Authenticated API and dashboard project
management are additive integrations; the standalone registry is not enabled
unless an embedding host supplies its dependencies.


## Authenticated project API integration

`api/projects.py` exposes the optional project registry through the authenticated
ASGI runtime when `project_registry` is supplied to
`build_conversation_runtime`. It requires the existing `TaskRuntime`; session
identity is resolved from the bearer token and is the only owner ID used by
the API. Request bodies cannot choose an owner.

Endpoints:
- `GET/POST /v1/projects` — list or create projects.
- `GET /v1/projects/{project_id}` — aggregate project progress from authorized task metadata.
- `GET /v1/projects/{project_id}/evidence` — list verified evidence provenance for current task versions only.
- `GET/POST /v1/projects/{project_id}/tasks` and `DELETE` — inspect, add, or remove explicit membership.
- `GET/POST /v1/projects/{project_id}/milestones` — list or create milestones.
- `POST /v1/projects/milestones/{milestone_id}/complete` — complete a milestone with expected version and verification reference.

The host must construct the registry with a task-owner resolver that delegates
to the same authenticated task authorization boundary, for example a resolver
that calls `TaskRuntime.get(actor_id=owner_id, task_id=task_id)` and returns
the record's immutable `owner_id`. The resolver receives both owner and task
ID to avoid an unsafe global task lookup. A project registry without a task
runtime is rejected during runtime construction.

Project progress reads at most 500 explicitly linked tasks and uses
`TaskRuntime.get` for each, so normal task-read authorization is enforced.
An optional `TaskEvidenceStore` can supply independently verified references;
without it, succeeded tasks remain `succeeded_unverified`. Milestone
verification references are recorded separately and are not automatically
treated as proof for individual tasks. No recommendations are dispatched
automatically.


## Project management in the Textual dashboard

When an embedding host supplies an owner-authenticated `ProjectRegistry` to
`run_dashboard(project_registry=...)`, the dashboard exposes:
- `/projects` — list projects belonging to the active dashboard actor.
- `/project create NAME` — create a project (multiword names supported).
- `/project PROJECT_ID` — show task-state progress and milestones.
- `/project add PROJECT_ID TASK_ID` and `/project remove PROJECT_ID TASK_ID` — manage explicit task membership.
- `/project milestone PROJECT_ID TITLE` — create a milestone.
- `/project complete MILESTONE_ID VERSION PROOF` — complete a milestone with an evidence reference and optimistic version check.

The dashboard does not manufacture an account identity. Standalone dashboard
launch remains unconfigured until an embedding host supplies the authenticated
actor, task runtime, and project registry. Task details are fetched through
`TaskRuntime.get`; unreadable tasks are reported as unavailable without
showing their definitions, results, or error payloads. Project progress remains
conservative: succeeded tasks without independently verified evidence do not
count as completed.


## Trusted task-evidence ledger

`core/task_evidence.py` adds an append-only, owner-scoped SQLite ledger for
CI workflow attestations, test reports, artifacts, and human reviews. Records
are accepted only after an injected host `attestation_verifier` returns
exactly `True`; exceptions and all other results fail closed. The verifier
receives the complete normalized attestation and must authenticate the
evidence source, verify the referenced task belongs to the stated owner, and
validate the evidence digest and provenance against that source. A boolean
callback that merely trusts caller-supplied fields is not a valid production
verifier.

The ledger bounds fields, requires a positive task version, SHA-256 digest and timezone-aware
verification timestamp, deduplicates by owner/task-version/digest/verifier, and blocks
SQL updates and deletes with append-only triggers. Reads are scoped by both
owner and task. The public authenticated project API has no evidence-ingestion
route: only the embedding host's trusted integration should call
`record_verified` after validating an external CI/review/artifact attestation.
The optional store is injected into `build_conversation_runtime`; project
progress then counts a succeeded task only when a matching owner/task/current-version evidence
record exists. `GET /v1/projects/{project_id}/evidence` exposes the current-attempt
provenance (kind, reference, digest, verifier and timestamp) to that project owner;
older-attempt evidence is excluded from both progress and this view. Without a configured trusted verifier, progress remains
conservative. A one-time schema migration preserves earlier ledger entries as task version 1.
This ledger records proof references; it does not run CI or certify an outcome by itself.


## GitHub Actions evidence integration

`integrations/github_actions_evidence.py` provides a narrow webhook adapter
for GitHub's `workflow_run` event. It verifies the raw-body
`X-Hub-Signature-256` HMAC before parsing, accepts only the `completed`
action, and requires exact repository, workflow-ID, and head-branch allowlists.
Fork-originated runs are rejected. Only a successful run with a matching
`https://github.com/<repository>/actions/runs/<id>` URL is eligible.

The workflow run title must contain an explicit correlation token:

```yaml
run-name: "[yuki-task:${{ inputs.yuki_task_id }}:v${{ inputs.yuki_task_version }}]"
```

The embedding host must provide a `task_binding_resolver(owner_id, task_id)`
that calls the existing owner-authorized `TaskRuntime.get` and returns the
current `(task.version, expected_source_commit_sha)` tuple. The adapter requires
both the exact current task version and exact 40-character Git commit SHA to
match the signed workflow event. It rejects missing tasks, owner mismatches,
stale versions, unrelated source commits, lookup errors, invalid signatures,
untrusted branches, and unallowlisted workflows. Duplicate deliveries are
idempotent through the evidence ledger's digest uniqueness constraint.

Production wiring requirements:
- Store the webhook secret in the host's secret manager; pass its bytes to the
  adapter and never log the secret or raw request body.
- Construct the evidence store with a verifier that accepts only the exact
  configured webhook, artifact and test-report verifier identities:
  `github-actions-webhook:<repository>:<workflow-id>`,
  `github-actions-artifact:<repository>:<workflow-id>`, and
  `github-actions-test-report:<repository>:<workflow-id>`. Keep the store
  private to trusted host integrations.
- Map each allowed repository to one immutable Yuki owner ID.
- Restrict branch allowlists to branches whose workflow definitions and
  permissions are reviewed; do not trust fork workflow runs.
- Mount the adapter behind a small HTTP route that forwards the unmodified raw
  body and the GitHub signature, event, and delivery headers. Do not parse and
  reserialize JSON before signature validation.
- Configure the workflow's run-name inputs from a trusted task dispatch
  record; never let an untrusted PR author choose a task ID/version that the
  host will certify.
- The repository includes `.github/workflows/yuki-task-evidence.yml`, a
  manual, single-runner workflow that checks out the dispatched source SHA,
  validates dependencies, compiles Python sources, runs the complete unittest
  suite, and uploads a bounded-retention test log. Dispatch it only on an
  allowlisted protected branch. Its workflow ID must be added to the adapter's
  exact allowlist.
- In GitHub repository Settings → Webhooks, register the Yuki HTTPS webhook
  route, select JSON and the `workflow_run` event, and use the same secret
  configured in the embedding host. Verify GitHub's delivery succeeds before
  enabling it for production tasks.
- Treat this as evidence of a successful workflow run only. It does not by
  itself prove every test assertion, artifact integrity, deployment health, or
  task completion semantics.

## Owner-authorized GitHub Actions task dispatch

`integrations/github_actions_dispatch.py` provides the dispatch side of the
verification flow. The host configures a fixed repository, workflow ID,
branch allowlist, a source-binding resolver, a private SQLite dispatch ledger,
and a GitHub App-backed workflow-dispatch callback. The callback must use an
installation token restricted to the configured repository and the Actions
write permission; it must not accept a repository, workflow, branch, or URL
from an untrusted caller. `GitHubAppWorkflowDispatchClient` supplies the
fixed REST endpoint, pinned repository/workflow identity, bounded request
contract and required GitHub headers. The host supplies the installation-token
provider (GitHub App JWT exchange/installation selection) and a transport that
verifies TLS, uses a bounded timeout and refuses redirects. The App installation
must be granted Actions: write for this repository.

The authenticated project API exposes:

```http
POST /v1/projects/{project_id}/tasks/{task_id}/dispatch-ci
Authorization: Bearer <Yuki session>
Content-Type: application/json

{}
```

The endpoint requires explicit `task:ci:dispatch` authorization and project
membership. It derives the owner from the session, reads the task through
`TaskRuntime`, and obtains the current (version, source SHA, branch) from the
trusted host resolver. The dispatcher pins the exact task version and SHA in
the workflow inputs and permits only configured branches.

The SQLite ledger reserves one dispatch per owner/task/version under an
immediate transaction. Duplicate dispatch attempts are rejected. If the
network outcome is ambiguous, the reservation becomes `outcome_unknown` and
is deliberately not retried automatically; an operator must reconcile the
GitHub run before any manual ledger repair. This avoids duplicate workflow
runs after a timeout where GitHub may already have accepted the request.

The workflow's run name carries the task/version correlation token. It checks
that the dispatched source SHA is a valid 40-character commit and equals the
workflow event SHA before running tests. It checks out that exact SHA with
persisted Git credentials disabled. The signed `workflow_run` webhook then
checks the same task version, source SHA, allowlisted repository/workflow and
branch before recording evidence. Artifact verification remains a separate
authenticated step.

Host wiring uses `github_actions_dispatcher` in
`build_conversation_runtime`. Configure a resolver that returns only a
current task's immutable version, commit SHA and allowlisted branch; never
derive these values from API request fields. The GitHub App callback should
call the workflow-dispatch REST endpoint with the supplied fixed workflow ID,
branch and three declared inputs. The webhook, artifact verifier and project
API continue to share the same task evidence store.

## GitHub Actions artifact and test-report verification

`integrations/github_actions_artifacts.py` adds a second, owner-scoped
verification stage. The embedding host supplies authenticated GitHub client
callbacks to fetch the exact workflow run, fetch the named artifact metadata
for that run, and download the artifact by repository and artifact ID. These
callbacks must use a least-privilege GitHub App installation credential and
must not accept arbitrary URLs from callers.

The verifier requires a matching successful `ci_workflow` webhook record
first. It then checks the exact run ID, repository, allowlisted workflow ID,
successful conclusion, source commit against the current task binding, artifact
name `yuki-test-report-<run_id>`, run association, expiry flag, size and
GitHub-reported SHA-256 digest. It downloads the archive, recomputes the digest,
opens the ZIP without extracting it, enforces archive/report size and entry
limits, and requires a successful unittest summary with at least one test.
It records separate append-only `artifact` and `test_report` evidence rows.

The authenticated project API exposes this operation only for tasks already
linked to a project owned by the current session:

```http
POST /v1/projects/{project_id}/tasks/{task_id}/verify-artifacts
Authorization: Bearer <Yuki session>
Content-Type: application/json

{"run_id": 123456789}
```

The host passes `github_actions_artifact_verifier` and the fixed
`github_actions_repository` to `build_conversation_runtime`. The verifier,
webhook and project API must share the exact same `TaskEvidenceStore`.
The project API derives task owner and current version from its authenticated
`TaskRuntime.get`; clients cannot submit an owner ID, task version, artifact
URL, digest, or repository. The response exposes test count and verified
digests, not the downloaded report contents.

This proves that GitHub's artifact bytes match the API-reported digest and
that the bundled unittest log reports a successful run. It does not prove
individual test quality, production deployment health, or that the tested
source was deployed. Artifact metadata and downloads remain dependent on the
host's authenticated GitHub client implementation.

The artifact client callbacks use these normalized response fields:
- Workflow run: id, workflow_id, conclusion, head_sha, head_branch, and either
  repository_full_name or repository.full_name.
- Artifact: id, name, expired, size_in_bytes, digest (GitHub's sha256:<hex>
  value), and either workflow_run_id or workflow_run.id.
- The download callback receives only the allowlisted repository and artifact
  ID, then returns raw ZIP bytes. It must use the authenticated GitHub artifact
  download endpoint and must not follow arbitrary caller-supplied URLs.

The adapter is mounted only when the embedding host explicitly supplies
`github_actions_evidence_webhook` to `build_conversation_runtime`; it shares
the exact `TaskEvidenceStore` instance used by project progress. The host must
configure the secret, allowlists, owner mapping, source-binding resolver and
request routing. The dedicated route is `POST /webhooks/github/actions` and
uses GitHub HMAC authentication rather than Yuki bearer sessions.


## GitHub Actions dispatch provenance

The optional GitHub Actions bridge now assigns a fresh opaque 128-bit
correlation ID to each owner-authorized task-attempt dispatch. The ID and the
owner/task/version/repository/workflow/source binding are reserved in the
durable dispatch ledger before the GitHub App request is sent. The workflow
requires that ID as an input and includes it in its run name. A signed
`workflow_run` webhook is accepted only when its repository, workflow,
task-version binding, source SHA, and correlation ID match a ledger row whose
dispatch reached the `dispatched` state.

The ledger atomically binds the correlation ID to one GitHub run ID. Repeated
delivery for that same run is idempotent; a different run cannot reuse the
same dispatch. A failed or uncertain dispatch remains fenced from automatic
retry until the host reconciles it. The correlation ID is not an authorization
credential by itself: GitHub's HMAC signature, the repository/workflow
allowlists, host-owned task binding, and the durable ledger are all required.
Legacy ledger databases receive additive nullable columns and retain their
existing dispatch reservations; legacy reservations without correlation IDs
cannot attest new webhook evidence.


## GitHub App Actions read client

`integrations/github_app_actions_read.py` provides a repository-pinned
read-side adapter for the existing artifact verifier. Construct one
`GitHubAppActionsReadClient` with a short-lived installation-token provider
and an injected HTTP transport that enforces TLS verification, bounded
timeouts, response-size limits, and no automatic redirects. Pass its
`workflow_run`, `artifact`, and `download_artifact` methods directly as
the artifact verifier's three GitHub callbacks.

The client normalizes authenticated workflow-run metadata, selects exactly
one artifact by its run-specific name, and downloads the artifact archive.
GitHub's signed object-storage redirect is validated against HTTPS and a
narrow host allowlist; the installation Authorization header is deliberately
not forwarded to that object-storage host. Caller-provided URLs are never
accepted. The host remains responsible for creating installation tokens with
only the required repository Actions read/write permissions and for injecting
the client into the runtime. This adapter does not enable a scheduler or
background dispatch on its own.


## Recovering uncertain GitHub workflow dispatches

A timed-out GitHub `workflow_dispatch` request is ambiguous because GitHub
does not return a run ID from the dispatch endpoint. Yuki therefore retains
the attempt reservation in `dispatching` or `outcome_unknown` and blocks a
second dispatch. The `dispatching` state is also reconciled after a host crash
that occurs between reservation and request settlement.
The host can reconcile it through the authenticated owner route:

`POST /v1/projects/{project_id}/tasks/{task_id}/reconcile-ci`

The route derives owner identity from the session and checks project task
membership and the existing `task:ci:dispatch` authorization. The dispatcher
uses an injected GitHub App run lookup to search the pinned workflow for the
opaque correlation ID, then verifies repository, workflow ID, and source SHA
against the durable reservation. Exactly one matching run changes the
reservation to `dispatched` and binds its run ID. No match, multiple matches,
lookup failure, stale task attempt, or mismatched metadata leaves the
reservation fenced; none permits an automatic retry. The subsequent signed
workflow webhook must use that same run ID.

The GitHub App read client exposes `find_dispatch_run` for this callback.
Inject it as `workflow_run_lookup` when constructing the task dispatcher.
When both dispatcher and webhook are enabled, the runtime requires them to
share the exact same dispatch-ledger instance so reconciliation and webhook
verification cannot consult divergent state.


## Task-scoped CI verification lifecycle

The authenticated project API exposes a read-only, task-version-scoped CI
verification view at:

`GET /v1/projects/{project_id}/tasks/{task_id}/ci-status`

It reports `pending`, `workflow_verified`, or `artifacts_verified` from
the trusted evidence ledger for the task's current version. The response also
includes the actual task state and a separate `task_completed` boolean.
A successful workflow webhook advances the verification view to
`workflow_verified`; verified test-report artifacts advance it to
`artifacts_verified`. A later task version naturally returns to
`pending` until that version has its own evidence.

This is deliberately a verification lifecycle overlay, not a task-state
transition. CI evidence is proof about a source revision, not proof that a
Yuki worker completed its assigned task. Only the task runtime can perform
terminal completion, using its existing expected-version, active-lease and
registered-worker-session fences. CI therefore cannot mark a task succeeded,
bypass a worker lease, or mutate the task's version while evidence is being
verified.


## Keep CI verification separate from task completion proof

Project intelligence now treats the evidence kinds as distinct signals.
`ci_workflow` records count as workflow verification, while the presence
of both `test_report` and `artifact` alongside a workflow record counts as
artifact verification. Neither signal alone counts as human-reviewed task
completion. The measured project completion numerator requires a current
task-version `human_review` attestation for a task already in the
`succeeded` state. This prevents a green CI run or a test artifact from
inflating completed-project progress.

The project dashboard reports the three dimensions separately: succeeded
tasks awaiting human review, workflow verification, and test-artifact
verification. These are evidence summaries, not automatic approvals or
permission to dispatch dependent work.


## Evidence-aware next-task prioritization

The native planner exposes `prioritize_project_work`, backed by a pure,
bounded project-intelligence helper. The host must supply an already
authorized project snapshot and owner identity. The shortlist separates
queued tasks whose dependencies have succeeded and carry current-version
human-review attestations from tasks blocked by missing, unfinished, or
unreviewed dependencies. Running and approval-waiting tasks are reported
separately.

This is an advisory shortlist, not a scheduler: it does not dispatch work,
change task state, grant capabilities, bypass approvals, or consume
untrusted task descriptions as instructions. Inputs pass through the
existing project snapshot owner, shape, and 500-task validation boundaries.
CI workflow/test-artifact evidence alone is not sufficient to unblock a
dependent task; dependencies require explicit human review.

## Persistent project awareness and safe resume context

`core/project_awareness.py` adds an additive, append-only SQLite history for
project goals, milestone notes, human-reviewed task outcomes, risks, and
advisory planning decisions. Every read and write requires an injected
project-authorizer callback that must return exactly `True`; the embedding
host must bind that callback to the authenticated project registry.

Records use a fixed event-kind allowlist, bounded summaries, bounded selected
task IDs, owner/project scoping, UTC timestamps, and database triggers that
reject updates and deletes. A `task_outcome_verified` event additionally
requires the exact task ID, positive task version, evidence reference, and an
injected outcome verifier that must return exactly `True`. In production,
that verifier must resolve the reference against the private
`TaskEvidenceStore` and accept only a current-version `human_review`
attestation. CI workflow, test-report, and artifact records are not sufficient.

The native planner exposes `recall_project_awareness` as an explicit,
bounded, authorized read operation. The returned records are typed history,
not trusted instructions; hosts should present them as provenance/context
and must not replay their summaries as executable commands. Planning decisions
remain advisory and never dispatch tasks, change permissions, or mutate task
state. This is durable operational memory, not model-weight training or a
claim of consciousness. The host must inject the store and trusted callbacks;
no default identity, scheduler, or background learning process is enabled.

### Authenticated project resume integration

The host can compose the store with `build_project_awareness_store(database_path,
registry, evidence_store, task_runtime)`. The verifier checks project membership and
re-reads the task through the owner-authorized runtime to require the exact current
version before accepting human-review evidence. This binds reads and writes to the existing
owner-scoped project registry and accepts verified outcomes only when the exact
task is a member of that project (including task references in planning decisions) and the current task version has a matching
`human_review` record in the trusted evidence ledger. CI, artifact, and test
report evidence are not accepted as human review.

When supplied to `build_conversation_runtime` as the optional
`project_awareness_store`, the authenticated project API exposes:

- `GET /v1/projects/{project_id}/awareness` — bounded recent history.
- `POST /v1/projects/{project_id}/awareness` — append one allowlisted event
  using session-derived owner identity.
- `GET /v1/projects/{project_id}/resume` — current member task snapshot,
  milestones, and recent awareness history in one bounded response.

These routes remain disabled unless the host injects the store, and runtime
composition requires the existing project registry, task runtime, and trusted
task evidence store. Resume responses explicitly label historical notes as
context rather than executable instructions. The endpoint does not dispatch,
retry, complete, or approve tasks.


### Evidence-aware project resume assessment

`build_project_resume_assessment` combines one bounded current task snapshot,
the latest recorded goal, recent risks, previous planning decisions, and
project milestone status. It reuses canonical progress aggregation and
dependency-aware next-task recommendations. It distinguishes human-reviewed
completion from succeeded-but-unreviewed tasks, lists ready and blocked task
IDs, and labels recommendations as advisory. It rejects history or milestone
records from another owner/project and bounds task, event, milestone, and
recommendation inputs.

The authenticated project resume endpoint now includes a structured
`resume_assessment` alongside its existing raw-but-redacted task, milestone,
and awareness snapshots. Current task state and exact-version evidence are
re-read at request time; awareness history cannot override them. CI evidence
is reported separately and cannot satisfy human-review completion. Artifact
verification requires the matching workflow, artifact, and test-report
evidence types. The assessment does not dispatch, approve, retry, or mutate
work. The planner exposes the same deterministic assessment through
`assess_project_resume` for other authorized host workflows.


### Fail-closed project execution readiness

`assess_project_execution_readiness` evaluates queued work against explicit,
bounded host-provided evidence: declared task capabilities, available worker
capabilities, worker availability, current task-approval authorization,
dependency completion with human review, task snapshot freshness, and
task-linked unresolved risks. Missing capability or worker evidence is
reported as review-required; stale snapshots, unresolved risks, and
unverified dependencies are blocked. A task is only listed as
`ready_for_approval` when every supplied check passes.

The authenticated project resume response includes this readiness report.
When the host injects the durable worker registry and an explicit
`worker_access_for(owner_id)` authorization callback, the API reads a bounded
fresh worker snapshot and aggregates only stable capability IDs advertised by
active workers. Stale, stopping, and stopped workers do not contribute.
`RegisteredSpecialistWorker` publishes capabilities only from the worker's
explicit `handler_capabilities` grants; the specialist factory carries
capabilities from the authenticated, enabled agent registry into those grants.
Caller-supplied metadata cannot overstate the configured grants.

Worker inventory is not exposed to project owners unless the host callback
returns exactly true. If no callback, registry, or fresh active worker exists,
availability remains unknown or false. When the host also injects the
owner-authorized `SpecialistAgentCoordinator`, the API retrieves required
capabilities from immutable owner-scoped agent-plan subtask records. The
coordinator checks `agent:read` at actor and plan scope and returns only
matching task IDs; tasks not created by a readable agent plan remain unknown.
The worker factory propagates those registered capability IDs to worker
handler grants, allowing the evaluator to compare task requirements against
active-worker advertisements. Required capabilities remain unknown when no
coordinator/plan record is available; available capabilities alone never prove
task compatibility. The API separately checks the session owner's task-approval
permission; this is not a dispatch grant.
Existing approval, coordinator authorization, and worker execution controls
remain mandatory. The evaluator is deterministic and read-only: it does not
approve, enqueue, dispatch, retry, or change task state.


## Execution contract preflight

The additive `core/execution_contract_readiness.py` evaluator checks a
versioned `AgentExecutionSpec` against an operator-configured
`SandboxExecutionProfile`, an explicitly supplied worker resource-capacity
attestation, and a host sandbox-verification result. It checks profile/spec
identity, requested runtime and memory budgets, and worker runtime, memory,
token, and step capacity. Missing evidence is `review_required`; incompatible
or failed evidence is `blocked`.

The `sandbox_verified` input is a trust boundary, not a value to derive from
Docker configuration or a worker's own metadata. The host must obtain it from
the same trusted runtime-verification path used immediately before execution,
bound to the exact image and sandbox policy, and must revalidate it at launch.
This preflight result is advisory and time-sensitive: it is not a reservation,
approval, authorization, or dispatch token. Execution must independently
recheck the current task lease, actor permissions, worker session, execution
spec, sandbox profile, and verifier evidence.

This evaluator is not yet wired into the authenticated project-resume API:
the project API has no authoritative per-task execution-spec/profile registry
or host-attested worker capacity provider. It therefore must not invent those
values from plan descriptions, generic worker metadata, or model output.
CPU capacity, runtime dependency availability, workspace mount compatibility,
and per-task artifact/output constraints remain future explicit contracts;
they are not claimed as verified by this preflight.
