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

At the time this verifier was first introduced, execution receipt fields were not persisted in the run schema. The subsequent durable-binding milestone below supersedes that limitation: run records now store the exact attempt, manifest, artifact, and receipt digests, and candidate-evaluation links retain those values. The verifier now requires those persisted fields and reopens the receipt journal. The two SQLite stores are still not one atomic transaction. Receipt hashes remain caller-produced integrity evidence, not a trusted sandbox attestation or authenticated owner identity. The implementation cannot prove that the benchmark runner loaded the artifact bytes whose digest appears in the manifest.


## Implemented milestone: durable execution-backed evaluation records

`EvaluationRunStore.record(..., execution_attempt_id=..., receipt_store=...)` now supports creating a run only after loading and validating the referenced owner-scoped manifest/receipt pair. It requires exact owner, candidate ID and candidate payload digest, benchmark ID/version/digest, and a successful non-timeout receipt. The run stores the attempt ID, manifest digest, artifact digest, and receipt digest in additive SQLite columns. Those fields participate in request identity and the immutable run record digest. Legacy rows migrate with empty execution fields and retain their previous verification semantics.

Candidate evaluation links now persist the same four execution evidence fields. `attach_evaluation` requires the receipt store when linking an execution-backed run and checks the receipt association. `list_evaluations` compares the link against the immutable run and reopens the receipt journal to verify the manifest and receipt digests. Legacy non-execution evaluation links remain supported and are not represented as execution-backed.

This improves durable association but does not make the separate run, receipt, and candidate-registry SQLite writes one atomic transaction. A crash can leave a valid run without a candidate link; reconciliation must be explicit. The receipt remains caller-generated integrity evidence, not an independently signed sandbox attestation or proof that the runtime loaded the artifact bytes. The caller-supplied owner label is not authentication. Tests were authored for persistence across reopen and exact evidence joins; they have not been run locally.


## Interrupted evaluation-link recovery

`EvolutionCandidateRegistry.reconcile_evaluation_link(...)` provides an explicit, idempotent recovery operation for the expected interruption window where an immutable evaluation run committed but the separate candidate-registry link did not. It loads the existing owner-scoped run, checks its candidate identity, verifies any existing links, and otherwise reuses the normal `attach_evaluation` validation path. Repeated or concurrent recovery attempts return the already verified link rather than creating another evaluation. Execution-backed runs still require the receipt store and exact persisted receipt association.

This operation does not rerun evaluation, synthesize missing receipts, change candidate review state, or automatically scan/reconcile all databases. Recovery must be invoked with an explicit owner, candidate, and run ID by a trusted orchestration path. It repairs only the missing registry association; it cannot make the separate SQLite commits atomic. If the receipt journal is missing or corrupt, recovery fails closed. A future supervisor can enumerate orphan runs and submit explicit recovery requests, but must preserve owner authorization and audit the action.


## Evaluation recovery supervisor

`evaluation/evaluation_recovery.py` adds a bounded owner-scoped recovery supervisor. Its `plan(...)` method inspects recent candidate-digest-bound runs, validates candidate state and any execution receipt association, and emits planned/already-linked/rejected items. Planning does not modify candidate or run stores. `apply(...)` is a separate explicit operation and delegates each planned item to the registry's idempotent reconciliation method, which rechecks current state and evidence at application time. This prevents a stale plan from bypassing a later candidate rejection or receipt change.

Recovery outcomes are written to a separate SQLite audit journal with application-level UPDATE/DELETE-blocking triggers. This is useful operational history, not independently witnessed or tamper-proof audit evidence: a privileged database owner can replace the database or remove triggers. The supervisor accepts owner IDs as caller-supplied labels and does not authenticate the caller. Production composition must place planning and apply behind the authenticated owner-approval boundary; the module itself is not that boundary. The scan is bounded to the requested most-recent run window and deliberately skips legacy runs without a candidate digest. No automatic background repair, evaluation rerun, promotion, or runtime mutation is performed.


## Independently signed runner-attestation boundary

The evaluation/runner_attestations.py module defines an Ed25519 verification contract. It
checks a public key pinned by trusted deployment configuration, owner and key identity,
key validity/revocation, timestamp freshness and clock skew, manifest identity, the
runner-reported digest of the artifact actually loaded, output digest, signature validity,
and one-time nonce consumption.

The verifier intentionally has no signing API. A valid signature establishes only that
the corresponding private key signed the payload. It does not prove runner isolation or
honesty unless the private key is available only to the trusted runner after it hashes
the exact bytes it loads. Never place a runner private key in the general Yuki process,
sign arbitrary caller-supplied digests, or treat a caller-provided runner label as
authenticated identity.

RunnerAttestationReplayStore atomically consumes (owner_id, key_id, nonce) in a private
SQLite journal. Nonces are bounded URL-safe random values; old entries are pruned after
bounded retention. The journal remains local application storage and privileged
filesystem/database access can tamper with it. Production still needs separately
protected runner identity, private-key custody, authenticated provisioning and
rotation/revocation, independent audit witnessing, and trusted composition. Until
those exist, attestations must not authorize candidate promotion, live self-modification,
or deployment. Verification requires the optional cryptography dependency.

Install the verifier's cryptographic implementation with `pip install .[runner-attestation]` (or the existing `production-storage` extra, which also includes `cryptography`). Test environments running `tests/test_runner_attestations.py` must install one of those extras.


## Linux sealed artifact staging primitive

`evaluation/runner_artifact_staging.py` adds a bounded Linux `memfd_create`
staging primitive. It checks the manifest and exact artifact digest, writes the
artifact into an anonymous memory-backed file, applies the kernel write/grow/shrink/
seal locks, verifies the contents again through the descriptor, and rewinds it for
the runner to consume. It avoids a mutable pathname between digest verification
and handoff. The descriptor is an explicit resource and must be closed by its owner.

The staging handle also exposes an explicit CLOEXEC descriptor duplicate for
controlled subprocess handoff. The caller should pass only that descriptor via
the subprocess API's `pass_fds` mechanism and close its copy promptly; it must
not expose the descriptor through environment variables or unrelated children.
This is a handoff primitive, not process isolation.


This primitive is not a sandbox, does not establish runner identity, and does not
protect against a compromised process that already holds the descriptor before
sealing or has equivalent kernel privileges. A future runner supervisor must pass
only this descriptor (for example, via a tightly controlled inherited file
descriptor), run a dedicated unprivileged worker with no ambient credentials or
network by default, and keep the Ed25519 private key in a separate protected
signing service or runner identity. The general Yuki process must not receive
signing capability. Non-Linux systems fail closed until an equivalent immutable
artifact handoff is implemented. No candidate execution, promotion, or live
self-modification is enabled by this staging module.


## Runner isolation policy contract (not an enforcement implementation)

`evaluation/runner_isolation_policy.py` defines a canonical, digest-bound
`RunnerIsolationPolicy` for the future Linux supervisor. The policy requires a
non-root UID/GID, a pinned seccomp-profile SHA-256 digest, default-deny networking,
read-only root filesystem, no-new-privileges, all Linux capabilities dropped,
and bounded CPU, memory, process count, wall time, output, and temporary storage.
Boolean values are type-checked separately from integers, and any relaxation
invalidates the policy.

This is deliberately a policy data contract only. It does not create namespaces,
mount filesystems, install seccomp filters, configure cgroups, drop credentials,
or launch a child process. No execution path may treat a valid policy digest as
proof that controls were enforced. The next supervisor milestone must apply the
controls in a dedicated unprivileged launcher and independently inspect the
resulting process/container state before execution. Until that exists, candidate
execution remains disabled behind this boundary.

## Linux artifact staging and isolation boundary (implemented contract)

`evaluation/runner_artifact_staging.py` stages the exact candidate artifact bytes
in a Linux memfd, applies the kernel write/size/seal locks, and verifies the
digest again. A receiving side can verify the descriptor's file type, size,
seals, and bytes without changing its shared file offset. The descriptor can
be duplicated for an explicitly selected subprocess handoff.

`evaluation/runner_isolation_policy.py` validates and fingerprints a
least-privilege policy: non-root UID/GID, bounded CPU/memory/PID/wall-time/output
limits, no network, read-only root filesystem, no-new-privileges, dropped
capabilities, and a pinned seccomp-profile digest.

These components are not an execution sandbox. The repository does not yet
provide a trusted launcher that creates namespaces, mounts a read-only root,
configures cgroups, installs seccomp, drops credentials, closes unrelated file
descriptors, and independently verifies the effective controls. The policy's
seccomp digest is only a syntactically validated pin; no profile is loaded or
installed here. The memfd handoff is not a process boundary and does not prove
which process consumed the descriptor. No candidate execution should be
enabled on the strength of these contracts alone. A future supervisor must
fail closed when any control cannot be applied and verified, and its runner
signing key must not be available to the Yuki orchestrator.

## Cgroup v2 resource-limit rendering

RunnerIsolationPolicy.cgroup_v2_settings() renders the validated CPU quota, memory maximum, zero swap allowance, and PID maximum using cgroup v2 file syntax (cpu.max, memory.max, memory.swap.max, and pids.max). The CPU field is expressed in thousandths of one CPU; the renderer uses a 100,000-microsecond period and converts the configured value to quota microseconds. For example, 1,000 milli-CPU renders as 1000000 100000.

This helper does not create or configure a cgroup. A future supervisor must create a private cgroup, write the values, read them back, verify controller availability and delegation, move the child into the cgroup before exec, and verify membership and effective limits. Failure at any step must prevent candidate execution. Rendering a valid cpu.max string is not enforcement.


### Cgroup v2 resource-limit read-back verification

`RunnerIsolationPolicy.cgroup_v2_settings()` renders the expected
`cpu.max`, `memory.max`, `memory.swap.max`, and `pids.max` values.
`verify_cgroup_v2_readback()` compares those values against the
supervisor's read-back mapping and fails closed for missing, non-string,
malformed, or mismatched entries, as well as an invalid policy digest.

This is a narrow configuration check, not proof of effective isolation. A
future supervisor must separately establish controller availability and
delegation, create a dedicated cgroup, apply and read back the values, place
the child in that cgroup before execution, and verify membership and effective
limits. It must also account for parent-cgroup limits and races. No process
launcher, cgroup mutation, or candidate execution is implemented by this
helper.

### Read-only cgroup v2 parent preflight

evaluation/cgroup_v2_preflight.py provides inspect_cgroup_v2_parent(), a read-only prerequisite probe for a configured cgroup v2 parent. It checks for the cgroup v2 control files, required cpu, memory, and pids controllers, whether those controllers are already enabled for child cgroups, and whether the parent directory is writable/searchable by the current process. The probe never enables controllers, changes permissions, creates a child cgroup, or writes cgroup.procs. Tests use a temporary control-file fixture and an injected permission checker.

A ready result is not an authorization to execute. It does not prove that the hierarchy is mounted at the expected host boundary, that parent limits leave sufficient capacity, that controller settings will remain stable, or that a child process can be placed before exec. Production must verify the actual mount and delegated subtree, create and configure a dedicated child, read back effective limits, place the child before untrusted code starts, and verify membership. Any missing prerequisite must stop execution; the probe must never try to repair host cgroup configuration automatically.

### Parent cgroup resource-ceiling verification

verify_parent_resource_ceiling() compares the validated runner policy with the parent cgroup's cpu.max, memory.max, memory.swap.max, and pids.max values. CPU quota is compared as a quota/period ratio rather than as raw quota text; finite memory and PID ceilings must accommodate the child policy, and kernel-reported max values are treated as unlimited. Missing, malformed, non-canonical, or insufficient values fail closed.

This check does not reserve resources, account for sibling consumption, prove ancestor hierarchy ceilings, or guarantee runtime availability. The production supervisor must inspect every relevant ancestor, calculate effective ceilings, account for the child cgroup's own limits, and revalidate immediately before execution. It must not infer available capacity from this helper alone.

### Full cgroup v2 ancestry verification

The read-only `verify_cgroup_v2_ancestry()` contract verifies the dedicated child cgroup's exact policy read-back, then checks `cpu.max`, `memory.max`, `memory.swap.max`, and `pids.max` at every visible ancestor through the supplied cgroup mount root. It rejects paths outside the resolved mount root, excessive ancestry depth, unreadable/malformed control files, child read-back mismatches, and any ancestor ceiling that cannot accommodate the requested policy. The mount root itself is included.

This is still a snapshot of configured ceilings, not a resource reservation or a complete proof of effective runtime capacity. Ancestors above the mounted hierarchy are not visible; sibling consumption and current memory/PID usage are not reserved; cgroup membership can change after inspection; and controllers may be affected by concurrent administrative changes. A production supervisor must bind the inspected mount to a trusted mount identity, inspect all visible ancestors, create and verify the child cgroup, place the process before exec, verify membership and effective controls immediately before launch, and continuously monitor enforcement. This helper does none of those actions and must never independently authorize execution.

### Cgroup v2 mount identity and process-membership snapshot

The read-only `verify_process_cgroup_membership()` contract parses the target process's unified `/proc/<pid>/cgroup` entry and mountinfo, requires exactly one matching cgroup2 mount, maps the kernel-reported cgroup path through the mount's root and mountpoint, and checks the expected directory's `cgroup.procs`. It records the mount ID and process start time, rejects malformed PID/path/mount records, and rechecks process start time, unified cgroup path, and cgroup.procs membership before returning success. Mountinfo octal path escapes are decoded strictly.

This is a point-in-time snapshot only. It does not pin an open mount/cgroup directory descriptor, authenticate a caller-supplied expected path, prevent migration immediately after the final read, prove that the target process shares the verifier's mount namespace, or close PID-reuse and path-substitution races against a hostile concurrent administrator. The caller must use the target process's actual mount namespace (or a trusted namespace-bound mount handle), compare the returned mount identity against trusted deployment state, retain directory descriptors, and use a supervisor-controlled fork/exec protocol that places the child into the verified cgroup before untrusted code can run. Never treat this helper's boolean result alone as permission to execute a candidate.


### Production runner activation gate

The current runner work is a set of verification primitives and an injected ordering contract, not an executable sandbox. Do not connect candidate evaluation to a live worker until all of these gates are implemented and independently reviewed:

- A privileged setup component creates a private cgroup and pinned directory descriptors; an unprivileged runner cannot choose or replace the trusted mount root.
- The launcher pins the artifact descriptor, starts a blocked child, applies and reads back the complete policy, verifies the child's cgroup membership through trusted descriptors, and only then releases exec.
- The runner process has no network, secrets, host writable paths, inherited sensitive descriptors, or ambient capabilities; seccomp is loaded and its installed identity is checked rather than merely accepting a digest string.
- Attestation signing keys are held by the trusted runner boundary, not by candidate code or the coordinator. The verifier pins key identity from deployment configuration and consumes replay nonces durably.
- Timeout, termination, reap, output bounds, cgroup emptiness, and resource cleanup are verified on every exit path. Any uncertainty fails closed and blocks promotion.
- Integration tests exercise the real Linux adapter in a disposable VM with adversarial fixtures; CI evidence and an explicit owner approval are required before enabling it.

Until those gates exist, candidate execution remains disabled. A valid policy, receipt, or signature by itself does not prove isolation, correct artifact loading, or safe execution; the current abstractions must not be represented as a production sandbox. No automatic self-modification, promotion, or deployment is authorized by this design.


### Runner cancellation cleanup

The supervisor coordinator now handles Python process-control exceptions (for example, `KeyboardInterrupt`) through the same child abort/reap and cgroup-empty cleanup gates as ordinary failures, then re-raises the original control exception. This avoids silently converting cancellation into a normal failed-attempt result. A regression test covers cancellation while waiting for the child. This remains an adapter contract; it does not establish kernel-level cleanup guarantees until a production adapter exists.


The cleanup path also preserves process-control exceptions raised during cleanup itself. It continues only the safe verification steps available, refuses cgroup removal when child reap or emptiness cannot be confirmed, and re-raises the interruption after cleanup attempts. Regression tests cover interruption during abort/reap and during the cgroup-empty check. This is best-effort orchestration: a real adapter still needs bounded, interruption-safe OS operations and an external supervisor for process death or host failure.
