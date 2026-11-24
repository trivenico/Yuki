# Audit 094 — Agent Runtime Identity, Workload Authentication, and Secure Worker Registration

Repository: trivenico/Yuki  
Base: main  
Scope: workflow worker claims, task progress and completion, agent lifecycle journal, inference worker authentication, application composition.  
Outcome: source audit; no runtime patch submitted.

## Executive summary

Yuki has durable task leases and useful concurrency controls. Workflow claims are serialized in SQLite, leases bind a task to a worker_id label, renewal uses compare-and-swap, and completion checks the current lease owner and expiry. The agent lifecycle journal checks that its configured worker_id matches the task lease before writing checkpoints. The paired-device inference worker has a stronger authentication design: device credentials are verified by the job service, claim tokens are random and lease-scoped, only token digests are persisted, and completion checks device authentication plus the lease token.

The critical distinction is that the workflow worker_id is an identifier supplied by the caller, not a cryptographically authenticated workload identity. The reviewed TaskRuntime APIs accept actor_id and worker_id as strings and delegate authorization to a generic callback. The SQLite lease store verifies equality of the supplied worker_id string; it cannot prove which process or workload supplied it. The agent journal adds a local check but uses the same caller-provided label. This is adequate as a concurrency fence among cooperative workers, not as a hostile-worker authentication boundary.

A second concrete boundary inconsistency remains: TaskRuntime.record_progress authorizes task:progress and checks that the task is running, but does not check that the caller is the current lease owner. AgentTaskJournal checks lease ownership before calling it, but direct callers can bypass that adapter if their authorization callback grants task:progress.

## Files inspected

- automation/task_runtime.py
- automation/workflow_store.py
- automation/agent_task_journal.py
- automation/inference_worker_supervisor.py
- automation/local_inference_worker.py
- automation/agent_coordinator.py
- core/auth.py
- core/permissions.py
- core/approval_flow.py
- api/runtime.py
- tests/test_task_runtime.py
- tests/test_agent_task_journal.py

## Positive controls observed

1. Workflow task claims use a SQLite BEGIN IMMEDIATE transaction and compare-and-swap update.
2. A claim records worker_id, lease expiry, state, attempt count, and version.
3. Lease renewal checks running state, worker label, version, and unexpired lease.
4. Completion checks running state, current worker label, and lease expiry; store transition uses versioned transactional event persistence.
5. AgentTaskJournal verifies active task state, worker label, lease expiry, and expected version before lifecycle checkpoints.
6. The inference worker uses device credentials; inference jobs store only SHA-256 digests of random lease tokens.
7. Inference completion verifies device authentication, job binding, lease status/expiry, and token digest; cancellation fences late completion.
8. Inference worker model allow-list, tools disabled, output bounds, and sanitized error codes reduce worker authority and data exposure.
9. Supervisor stops on device authentication failure rather than retrying indefinitely.

## Findings

### P1 — Workflow worker IDs are caller-supplied labels, not authenticated workload identities

TaskRuntime.renew_lease and complete accept worker_id as a string. WorkflowRunStore.claim_next accepts worker_id as a string and persists it as lease_owner. The store correctly compares the supplied label to the persisted label, but no cryptographic credential or authenticated workload principal is bound to that label in these interfaces.

The generic TaskRuntime authorize callback receives actor, action, and task ID. It does not receive a verified worker credential, workload identity, audience, or attestation. The inspected AgentTaskJournal also receives worker_id from its constructor and compares that value with the persisted lease owner.

Risk: if an untrusted caller can reach these APIs and obtain task-level authorization, it may present a known worker label. The lease equality check prevents accidental stale-worker writes, but does not independently authenticate the process making the call. This is not proof of an exploitable remote endpoint; it is a missing trust contract at the runtime API boundary.

Required direction:
- Introduce authenticated workload principals issued by a trusted worker identity service.
- Bind claims to workload ID, tenant, worker pool, audience, expiry, and key ID.
- Bind the lease to the authenticated principal ID, not a free-form worker string.
- Verify identity and task/action/resource scope at every claim, renewal, progress, artifact, failure, and completion boundary.
- Use short-lived signed credentials with rotation and revocation; do not expose long-lived shared worker secrets to agent prompts or tools.
- If workers run in containers/VMs, bind identity issuance to trusted orchestrator/runtime evidence rather than self-reported labels.

### P1 — Direct task progress writes do not enforce current lease ownership

TaskRuntime.record_progress checks task:progress authorization and that the task is running, then appends a versioned event. It does not accept a worker identity or compare the caller to the active lease owner. AgentTaskJournal performs a lease check before invoking record_progress, but record_progress is public and can be called directly.

Risk: a caller with task:progress authorization can append progress events while another worker owns the task lease, unless the injected authorization callback separately enforces the current worker. The lowest-level API does not guarantee the lease invariant.

Required direction:
- Move lease-owner and expiry enforcement into the transactional store operation that appends worker progress.
- Require an authenticated worker principal as an argument and compare it inside the same transaction as the event append/version increment.
- Keep a separate supervisor/operator progress operation with an explicit, audited capability rather than bypassing worker checks.
- Add direct-call regression tests proving a non-owner and an expired worker cannot append progress, including concurrent lease replacement.

### P1 — Task owner/tenant and worker identity are not intrinsically bound in the workflow schema

The reviewed workflow_runs schema stores task ID, global idempotency key, workflow definition, state, lease owner, and approval metadata, but no owner/tenant or authenticated workload principal. TaskRuntime delegates access decisions to an injected callback. That callback can enforce policy, but the persistence layer cannot independently establish tenant ownership or prove that a task belongs to the principal being presented.

Risk: correctness and isolation depend on every caller wiring an authorization callback that performs the intended owner/tenant checks. Alternate composition paths may accidentally provide weaker callbacks.

Required direction:
- Persist tenant and creator principal on task creation.
- Scope idempotency uniqueness to tenant/principal as appropriate.
- Bind task reads, events, claims, and transitions to tenant and authenticated worker principal.
- Make missing authorization dependencies fail closed and validate production runtime composition explicitly.

### P2 — Worker revocation and credential rotation are not a unified lifecycle

The inference worker has device credential revocation and lease-token fencing. The workflow worker contract has a worker label and lease expiry, but the inspected workflow store does not expose an authenticated worker registry with credential status, key rotation, revocation epoch, or worker drain state.

Risk: disabling an agent or worker label does not by itself prove that already-issued worker authority is revoked across all running tasks and components.

Required direction:
- Add worker registration states: pending, active, draining, revoked, retired.
- Check credential revocation epoch on claim and every protected transition.
- Revoke outstanding leases or allow explicit bounded drain according to incident policy.
- Audit registration, credential rotation, revocation, and retirement events.
- Keep agent desired state (enabled/disabled) separate from worker credential state.

### P2 — Agent identity, agent definition, and worker identity are separate but not cryptographically linked

The specialist registry describes agent capabilities and permissions; workflow workers claim tasks by worker_id; AgentTaskJournal writes lifecycle checkpoints using actor_id plus worker_id. The reviewed interfaces do not bind the immutable agent manifest revision, authenticated worker principal, task grant, and policy revision into one verifiable execution context.

Risk: audit records can describe which string was used as actor or worker, but cannot prove which approved agent artifact ran under which authority.

Required direction:
- Issue a signed execution grant binding tenant, initiating principal, agent ID, manifest digest, worker principal, task ID, allowed actions/resources, policy revision, and expiry.
- Verify the grant at dispatch and at each privileged side-effect boundary.
- Include grant ID and manifest digest in lifecycle events without storing bearer credentials.

## Recommended implementation sequence

1. Define a trusted workload identity issuer and verifier.
2. Bind workflow claims and leases to authenticated workload principals.
3. Move progress/artifact/failure/completion lease enforcement into transactional store methods.
4. Add tenant/creator ownership to workflow records and tenant-scoped idempotency.
5. Add worker registration, rotation, drain, and revocation lifecycle.
6. Bind signed task execution grants to agent manifest digest and policy revision.
7. Add adversarial tests for forged worker labels, direct progress writes, expired/revoked credentials, cross-tenant task access, and concurrent lease replacement.
8. Integrate worker identity with the central AgentControlPlane roadmap.

## Validation and limitations

This is a source inspection of the listed paths. No tests were executed. No worker identity issuer, orchestrator workload attestation, remote worker endpoint, multi-tenant production deployment, or live impersonation attempt was exercised. The finding about worker IDs describes the reviewed API contract; it is not a claim that a public unauthenticated endpoint currently exposes these methods.

No runtime patch was submitted. A local string comparison or optional worker_id parameter would not create authenticated workload identity and could create a false sense of security. The progress-write invariant needs to be enforced transactionally together with a trusted worker principal.
