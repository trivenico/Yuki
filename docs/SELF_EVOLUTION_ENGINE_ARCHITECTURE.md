# Yuki self-evolution engine architecture

Status: proposed architecture; implementation is incremental and owner-controlled.

## Product objective

Yuki should become more useful through repeated owner interactions by learning explicitly
approved preferences, retaining verified lessons, improving prompts and workflows, and
evaluating candidate changes against representative tasks. "Self-evolution" does not mean
unrestricted self-modification, silent permission expansion, or automatic production deploys.

The target is a private, single-owner assistant. There is no public signup, tenant model,
or public agent marketplace in this design.

## Existing foundations (not a complete learning system)

The repository already contains several useful but separate foundations:

- `companion/memory_consent.py`: session-scoped explicit consent for memory writes and
  separate consent for including saved memories in model context.
- `companion/secure_memory.py`: encrypted local memory storage and portable encrypted
  backup support.
- `yuki_learning.py`: bounded local task-outcome history with secret-pattern redaction
  and lesson retrieval. Its module contract explicitly says it does not train or modify
  the model.
- `companion/evolution.py`: opt-in local daily analysis of explicitly recorded observations.
  It emits review proposals; it does not call a model/network, modify code/configuration,
  train a model, or execute actions.
- `evaluation/`: benchmark registry, comparison, evaluation service, and run persistence.
- `skills/`: a growing capability collection, with separate approval/trust boundaries.

These are not yet one integrated, evidence-backed self-improvement loop. In particular,
a recorded observation is not automatically a verified fact; a lexical pattern is not
proof of preference; a reviewed proposal is not an applied change; and benchmark
infrastructure does not by itself authorize promotion.

## Target architecture

```text
Owner interactions and explicit feedback
              |
              v
  Consent + data minimization gateway
              |
              v
   Observation / outcome journal
              |
              v
  Lesson and preference candidate miner
              |
              v
  Evidence-backed candidate registry
              |
              v
  Evaluation and security gate
              |
              v
  Isolated experiment / shadow execution
              |
              v
  Owner approval + signed promotion record
              |
              v
     Versioned active configuration
              |
              v
  Monitoring, regression detection, rollback
```

Every stage must have a typed contract and durable provenance. The learning pipeline
must be able to stop at any stage without changing live behavior.

### 1. Observation gateway

Only capture data when the owner has enabled the relevant feature and the interaction
is within the declared purpose. Keep distinct controls for:

- remembering a durable preference;
- retaining task outcomes or lessons;
- using saved memory as model context;
- including a sample in evaluation datasets;
- using data for any future training or fine-tuning.

Default each optional use to off until explicitly enabled. Provide inspect, export, retention,
and deletion paths. Never treat access to a conversation as blanket consent to retain it
or train on it.

Before persistence: bound input size, redact common secrets, classify sensitive content,
record source and timestamp, and reject malformed records. Secret redaction is best-effort,
not a guarantee; never persist credentials as learning data.

### 2. Observation and provenance journal

A journal record should contain:

- immutable record ID and schema version;
- observation type (task outcome, explicit correction, preference, failure, verified lesson);
- source interaction/task reference, where available;
- creation time and retention class;
- owner-consent scope at capture time;
- redaction/classification result;
- confidence and verification status;
- content digest and provenance metadata.

Treat imported documents, web pages, tool output, and model-generated text as untrusted
evidence. They may contain prompt injection and must never become system policy or grant
permissions. Do not merge contradictory observations without preserving their sources.

### 3. Candidate miner

The miner may propose, but not silently apply:

- stable communication preferences;
- reusable task lessons;
- prompt/template revisions;
- skill/workflow improvements;
- routing or model-selection changes;
- benchmark additions.

Each candidate must cite the observations and task runs supporting it, state uncertainty,
describe the expected benefit, identify affected components, and include a rollback plan.
Repeated frequency alone is not enough: distinguish repeated user instruction from
quoted text, imported content, one-off requests, and accidental repetition.

### 4. Candidate registry and immutable versions

Represent each proposal as a versioned artifact with:

- candidate ID, type, parent version, and content digest;
- source observation IDs and evaluation dataset version;
- owner intent and expected measurable outcome;
- permissions required (normally none beyond the existing baseline);
- affected prompts, skills, configuration, or code paths;
- security/privacy impact assessment;
- test/evaluation results and reviewer decision;
- expiry, status, and rollback target.

Candidate states: `proposed`, `needs_evidence`, `evaluating`, `rejected`,
`approved`, `staged`, `active`, `rolled_back`, `expired`.

State transitions must be explicit and audited. A model or specialist cannot approve its
own candidate. The active version remains immutable; changes create a new version.

### 5. Evaluation gate

Evaluate candidate and baseline on the same versioned task set. Use deterministic tests
where possible and repeated model evaluations where outputs are stochastic.

Minimum scorecard:

- task success and instruction adherence;
- factuality and citation correctness;
- privacy leakage and secret-handling tests;
- prompt-injection and tool-abuse resistance;
- authorization and approval compliance;
- latency, token/compute cost, and failure rate;
- communication quality and user preference fit;
- regression rate on previously passing tasks.

Set thresholds before the experiment. A candidate must not trade away security, privacy,
or authorization correctness to improve style or task scores. Record raw results, model and
provider versions, prompt hashes, evaluation-set version, and evaluator version.

### 6. Isolated experiments

Run candidate prompts, skills, and code in a disposable, least-privilege environment.
Use synthetic or explicitly approved evaluation data. No production secrets, unrestricted
network, owner filesystem, or live integrations in the experiment sandbox.

Shadow evaluation can compare outputs without executing side effects. Any action test must
use mocks or disposable fixtures. A candidate that fails or times out is rejected or returned
for more evidence; it is never silently retried with broader permissions.

### 7. Owner approval and promotion

Require a fresh owner approval bound to the exact candidate digest, target environment,
requested permissions, and evaluation report. Approval expires and cannot be reused for
a different artifact.

The first production-capable releases should support only low-risk promotion targets:
approved memory/preferences and non-executable prompt templates. Code, dependencies,
permission manifests, provider credentials, security policy, and deployment changes require
a separate reviewed engineering release process and CI—not an agent self-approval.

Promote atomically, retain the prior known-good version, and verify the active digest after
promotion. If health checks or regression monitors fail, stop using the candidate and restore
the previous version. Record who approved, what changed, when it changed, and why.

### 8. Continuous monitoring and rollback

Monitor candidate-specific task outcomes and safety signals. Roll back on security/privacy
regressions, authorization violations, critical factuality regressions, or agreed quality
threshold failures. Do not let the candidate alter its own monitoring, evaluation set, audit
trail, or rollback controls.

## Communication quality ("humanizer")

The communication layer should become more natural, clear, empathetic, and context-aware
without pretending Yuki is human or fabricating emotions, memories, credentials, or lived
experience.

Evaluate it on:

- directness and relevance;
- clarity and useful structure;
- tone matching the owner's stated preference;
- appropriate warmth without excessive flattery;
- calibrated uncertainty and honest capability statements;
- preservation of the owner's intent and voice;
- avoidance of repetitive templates and unnecessary verbosity.

Use owner-approved examples and explicit corrections. Do not optimize for emotional
dependency, manipulation, deception, or maximizing engagement time. "Human-like" means
high-quality human-centered communication, not false claims of being human.

## Quantum and advanced-compute research

Do not describe conventional model inference or software optimization as quantum computing.
A future accelerator abstraction may compare classical CPU/GPU/TPU and genuine quantum
services where a concrete workload, hardware, SDK, security model, and measurable advantage
exist. Quantum-inspired optimization is a separate research label and must be benchmarked
against classical baselines. No quantum capability is assumed or required for the evolution
engine.

## Security invariants

1. Learning never grants new tools, network access, filesystem scope, credentials, or roles.
2. Untrusted content is data, never instructions or policy.
3. The owner can inspect and control what is retained and where it is used.
4. Candidate generation is separate from candidate approval and activation.
5. Evaluation cannot use production credentials or perform live side effects.
6. Every promotion is bound to an immutable digest and fresh authorization.
7. Audit and rollback controls are outside candidate control.
8. Failure, uncertainty, and unavailable evidence fail closed.
9. Model training/fine-tuning is a separate opt-in data-governance workflow.
10. No candidate can remove tests, lower safety thresholds, or rewrite its own gate.

## Delivery sequence

1. **Inventory and contracts:** map existing memory, learning, evolution, evaluation, and
   skill systems; define typed schemas and data-flow boundaries.
2. **Safe observation journal:** unify opt-in event capture, retention, redaction, provenance,
   and owner inspection without automatic context injection.
3. **Evidence-backed candidate registry:** add immutable candidate records and explicit
   review states; do not auto-apply.
4. **Evaluation integration:** connect candidates to versioned benchmarks, baseline comparison,
   privacy/security tests, and recorded evaluation runs.
5. **Sandbox and shadow mode:** test candidate behavior without production credentials or
   side effects.
6. **Owner-approved low-risk promotion:** start with preferences and prompt templates,
   exact-digest approval, atomic activation, and rollback.
7. **Skill/workflow evolution:** extend only after isolation, policy enforcement, CI, and
   recovery have passed release gates.
8. **Model adaptation research:** separately assess retrieval, prompt optimization, adapters,
   and fine-tuning using explicit data consent and held-out evaluation sets.

## Definition of done

The evolution engine is not production-ready until it has tested schemas and migrations,
concurrent-writer safety, bounded storage, secret/sensitive-data handling, consent revocation,
provenance integrity, prompt-injection resistance, candidate tampering detection, approval
replay prevention, sandbox isolation, evaluation reproducibility, atomic promotion, rollback,
and crash recovery. Documentation and proposal generation alone do not satisfy this bar.


## Implemented milestone: proposal-only candidate registry

The first registry slice is implemented in `evaluation/evolution_candidates.py`
with coverage in `tests/test_evolution_candidates.py`. It provides:

- SQLite persistence through the existing private-file preparation helper,
  owner-scoped reads, bounded candidate count and payload size.
- Canonical JSON payloads with SHA-256 content digests, immutable candidate
  rows, and append-only review-event writes through the registry API.
- Review writes bound to the exact candidate digest and expected revision,
  with a transactional state check to reject stale or repeated decisions.
- A deliberately narrow review vocabulary: `needs_evidence`, `rejected`,
  and `accepted_for_evaluation`.

This is a proposal/review journal only. `accepted_for_evaluation` is not
approval to activate a candidate, change configuration, train a model, execute
code, or invoke tools. The registry has no evaluator, promotion adapter,
runtime configuration writer, network client, or execution path. Candidate
text and evidence references are data, never instructions to execute.

Security boundary limitations are explicit: `owner_id` and `reviewer_id`
are caller-supplied labels, not authenticated identities or authorization
proof. SHA-256 detects accidental payload corruption when read, but a
privileged actor able to rewrite the SQLite database can also rewrite its
digest; this is not an externally witnessed or non-repudiable audit log.
Filesystem privacy is defense in depth and does not protect against another
process running as the same OS account. The separate authenticated owner
approval and independently verifiable promotion record remain future work.


## Implemented milestone: evaluation evidence binding

The proposal registry now exposes `attach_evaluation` and
`list_evaluations`. Linking is permitted only after the candidate has reached
`accepted_for_evaluation`, and requires the exact candidate content digest.
The supplied completed run is loaded through `EvaluationRunStore` and its
record integrity is verified before linking. Owner and candidate IDs must
match.

The immutable link stores the candidate digest, evaluation run ID and record
digest, report digest, benchmark ID/version/digest, and link timestamp.
Repeated links are rejected. Reads re-load and re-verify the referenced run
and compare all stored run/report/benchmark digests, failing closed if the
underlying evaluation record changes.

This is evidence association, not an evaluator or promotion gate. The registry
does not decide whether scores meet thresholds, compare baseline runs, infer
that a benchmark is representative, approve activation, or change live
configuration. The run store's existing `candidate_id` is an identifier; it
does not prove that the evaluated runtime artifact was byte-for-byte derived
from the candidate payload. That stronger artifact-to-execution binding
requires a future typed candidate adapter and sandbox execution manifest.
Caller-supplied owner IDs remain labels, not authentication.


## Candidate digest binding in evaluation run records

The evaluation run journal now carries an optional `candidate_digest` field.
When supplied, it participates in the request digest and immutable run-record
digest, so idempotency and record verification distinguish different candidate
artifacts even when their human-readable candidate ID is unchanged. Existing
records are migrated with an empty digest and retain legacy verification
semantics; they cannot be attached as digest-bound candidate evidence.

The candidate registry's evidence-link operation requires the run's recorded
candidate digest to equal the reviewed candidate payload digest. This closes
the previous gap where only the candidate ID was associated with a report.

This is still a caller assertion, not proof of execution provenance. A future
sandbox runner must independently hash the exact staged artifact, record the
sandbox image/runtime and input manifest, and produce a signed or otherwise
independently verifiable execution receipt. Until then, digest binding proves
what the run record claims it evaluated, not what a process actually loaded.


## Implemented milestone: candidate execution evidence contracts

Added `evaluation/execution_receipts.py` with bounded immutable data contracts
for a candidate execution manifest and completion receipt. Manifest creation
hashes the exact artifact and input-manifest byte strings supplied by the
caller, then binds those digests to the reviewed candidate, benchmark identity,
attempt ID, sandbox image label, runtime identity, runner version, and a
timezone-aware timestamp. The receipt binds the manifest digest, artifact
digest, output digest/byte count, exit status, timeout flag, and start/end
times. Raw output is not retained. Both contracts use canonical JSON and
SHA-256 integrity verification; inputs and record sizes are bounded.

Focused tests cover exact-byte digest calculation, manifest/receipt tampering,
attempt/manifest mismatch, malformed types, timestamp ordering, and size/type
bounds. Tests are authored but have not been run locally.

**Trust limitation:** this is an evidence schema and hashing helper, not a
sandbox runner, durable receipt store, signature, or remote attestation system.
The caller supplies artifact bytes and runtime labels; the helper cannot prove
that a sandbox mounted or executed those bytes, nor authenticate the caller.
A privileged actor able to replace both a record and its digest can forge this
integrity-only evidence. The next integration must have the trusted staging
and runner boundary read back staged bytes, enforce read-only mounts and
isolation, persist the receipt append-only, and verify a signature from a
separately protected runner identity. No candidate execution or promotion is
enabled by this module.


## Cross-store execution evidence verification

`evaluation/execution_evidence_binding.py` adds a read-time join between a completed `EvaluationRun` and a stored candidate execution manifest/receipt. It verifies the run record, loads the owner-scoped receipt by attempt ID, and requires exact owner, candidate ID/digest, benchmark ID/version/digest, manifest digest, artifact digest, and attempt bindings. Non-zero exit codes and timed-out attempts are rejected. The returned immutable binding contains the run record, manifest, artifact, and receipt digests for a future durable association.

This is deliberately a verifier, not a claim that the evaluation run itself is now receipt-bound: the current `EvaluationRun` schema does not persist execution receipt fields, and this helper does not mutate it or the candidate registry. A caller must not label the run execution-backed solely because a verifier returned a binding unless that binding is subsequently persisted and re-verified with the run. The two SQLite stores are not one atomic transaction. Receipt hashes remain caller-produced integrity evidence, not a trusted sandbox attestation or authenticated owner identity. The helper also cannot prove that the benchmark runner loaded the artifact bytes whose digest appears in the manifest.


## Implemented milestone: durable execution-backed evaluation records

`EvaluationRunStore.record(..., execution_attempt_id=..., receipt_store=...)` now supports creating a run only after loading and validating the referenced owner-scoped manifest/receipt pair. It requires exact owner, candidate ID and candidate payload digest, benchmark ID/version/digest, and a successful non-timeout receipt. The run stores the attempt ID, manifest digest, artifact digest, and receipt digest in additive SQLite columns. Those fields participate in request identity and the immutable run record digest. Legacy rows migrate with empty execution fields and retain their previous verification semantics.

Candidate evaluation links now persist the same four execution evidence fields. `attach_evaluation` requires the receipt store when linking an execution-backed run and checks the receipt association. `list_evaluations` compares the link against the immutable run and reopens the receipt journal to verify the manifest and receipt digests. Legacy non-execution evaluation links remain supported and are not represented as execution-backed.

This improves durable association but does not make the separate run, receipt, and candidate-registry SQLite writes one atomic transaction. A crash can leave a valid run without a candidate link; reconciliation must be explicit. The receipt remains caller-generated integrity evidence, not an independently signed sandbox attestation or proof that the runtime loaded the artifact bytes. The caller-supplied owner label is not authentication. Tests were authored for persistence across reopen and exact evidence joins; they have not been run locally.
