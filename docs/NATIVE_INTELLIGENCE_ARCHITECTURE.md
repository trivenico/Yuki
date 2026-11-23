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

The registry is intentionally a separate injected component: existing
workflow tables and their layout are not restructured. API and dashboard
composition are follow-on integration work; the standalone registry is not
automatically exposed as an endpoint or command.


## Authenticated project API integration

`api/projects.py` exposes the optional project registry through the authenticated
ASGI runtime when `project_registry` is supplied to
`build_conversation_runtime`. It requires the existing `TaskRuntime`; session
identity is resolved from the bearer token and is the only owner ID used by
the API. Request bodies cannot choose an owner.

Endpoints:
- `GET/POST /v1/projects` — list or create projects.
- `GET /v1/projects/{project_id}` — aggregate project progress from authorized task metadata.
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
The API deliberately has no source for independent task completion evidence
yet: succeeded tasks therefore remain `succeeded_unverified` and measured
verified progress remains conservative until a trusted evidence adapter is
provided. Milestone verification references are recorded separately and are
not automatically treated as proof for individual tasks. No recommendations
are dispatched automatically.
