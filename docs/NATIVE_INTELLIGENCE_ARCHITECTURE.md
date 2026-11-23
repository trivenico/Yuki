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
