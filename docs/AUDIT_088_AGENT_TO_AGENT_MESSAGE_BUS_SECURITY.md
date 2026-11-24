# Audit 088 — Agent-to-Agent Communication Security and Secure Message Bus

**Repository:** trivenico/Yuki  
**Base reviewed:** main  
**Scope:** specialist-agent orchestration, durable task runtime, task event journal, workflow worker claims, agent execution callbacks.  
**Outcome:** architecture/security audit; no runtime patch submitted.

## Executive summary

The inspected code provides a useful durable task coordination substrate: task creation and transitions are persisted, task events have per-task sequence numbers, event reads require task-level authorization, worker claims use atomic leases, and the agent lifecycle journal excludes prompts/tool arguments/tool outputs from its event payloads.

However, this is not yet a complete authenticated agent-to-agent message bus. The reviewed interfaces do not establish a cryptographically verifiable message envelope binding sender agent, recipient agent, tenant, task, message purpose, expiry, and payload digest. The task event stream is a workflow journal, not a general recipient-addressed messaging protocol. Authorization callbacks and trusted application composition remain central to the identity boundary.

This is an interface-level assessment of the inspected paths. It does not demonstrate a live message spoofing exploit or establish that every deployment exposes these APIs to untrusted agents.

## Files inspected

- automation/agent_coordinator.py
- automation/agent_task_journal.py
- automation/task_runtime.py
- automation/workflow_store.py
- automation/workflow_engine.py
- core/agent.py
- core/agent_execution_loop.py
- tests/test_agent_coordinator.py
- tests/test_agent_task_journal.py

## Positive controls observed

1. Specialist-agent routing is capability-based, deterministic, and selects the least-overprovisioned enabled specialist.
2. Agent plans validate bounded subtasks and dependencies; dispatch is dependency-gated.
3. Task events are persisted with a per-task monotonically increasing sequence and unique task/sequence constraint.
4. Task event reads are bounded and require task:read authorization for the task.
5. Worker claims are serialized and lease-fenced; only a current worker lease can complete a running task.
6. Task state changes use version checks and durable event insertion in a transaction.
7. AgentTaskJournal checks that the supplied worker owns the active task lease, checks expiry, and uses compare-and-swap versions before recording lifecycle checkpoints.
8. Journal events intentionally exclude prompts, tool arguments, and tool outputs; selected metadata is bounded and sanitized.
9. Agent execution treats tool outputs as untrusted evidence, and orchestration tests cover dependency gating, registry changes, authorization, and task lifecycle behavior.

## Findings

### P1 — No verifiable agent-message envelope or recipient binding in the inspected task/event interfaces

The task event schema records task ID, sequence, event kind, actor ID, payload JSON, and timestamp. It does not persist a signed message envelope with authenticated sender-agent identity, recipient-agent identity, tenant scope, audience, message purpose, expiry, nonce/sequence, payload digest, or delegation-grant reference. The event stream is task-scoped rather than recipient-addressed.

The coordinator assigns specialist IDs to subtasks, but that assignment alone does not cryptographically bind later messages/results to the intended recipient or authenticate the worker's agent identity independently of its task actor/worker identifiers.

**Risk:** application code may treat a task event or task result as an authenticated statement from a particular specialist when the inspected persistence contract only establishes task-level actor metadata and state.

**Required direction:** define a versioned message envelope. Bind sender and recipient agent IDs, tenant/principal, task/plan, message type, expiry, nonce/sequence, content digest, and capability/delegation grant. Authenticate with a service-held signing key or mutually authenticated transport. Validate authorization and audience at receipt, not just send time.

### P1 — Task progress writes are not intrinsically bound to the active worker lease

AgentTaskJournal verifies the active worker lease before calling TaskRuntime.record_progress. However, the lower-level record_progress method itself checks the configured task:progress authorization callback and that the task is running, then appends an event with optimistic versioning. It does not itself compare the caller/worker identity with the current lease owner.

This means the worker-binding guarantee depends on every caller using the journal adapter correctly and on the authorization callback being scoped narrowly enough. A caller that is granted task progress authority for a task can append progress events while another worker owns the lease unless the trusted authorization callback separately prevents it.

**Risk:** forged or misleading lifecycle events can enter the durable task stream and be consumed as coordination signals or operational evidence.

**Required direction:** make worker identity a required argument for worker-originated progress/artifact/completion operations and enforce lease ownership and expiry inside the transactional store operation. Separate supervisor-authored administrative events from worker-authored events and identify both explicitly.

### P1 — Task/plan ownership and tenant scope are not carried through the message stream

The task event schema has no owner/tenant column, and the task record/event APIs rely on a trusted authorization callback to scope access. Agent plan records likewise persist plan identifiers and idempotency metadata without a principal/tenant owner field. The coordinator's authorize(actor, action, resource) callback is therefore the enforcement point; the persisted plan/task data cannot independently assert its tenant boundary.

**Risk:** a misconfigured or inconsistent authorization callback across API, worker, dashboard, and event-consumer components could permit cross-principal task/event access.

**Required direction:** persist immutable tenant and owner identifiers on plan/task records, derive them from authenticated identity, and enforce them in every query and transition. Add cross-tenant negative tests for task detail, event streams, worker claim, cancellation, result retrieval, and plan lookup.

### P2 — Event sequence is not a replay-resistant authenticated message protocol

Per-task event sequence numbers and optimistic versioning prevent duplicate sequence values and stale state updates. They do not by themselves authenticate event origin or prevent a consumer from replaying an old event after reconnect, switching task contexts, or accepting a copied payload.

**Required direction:** consumers should persist their last accepted sequence per authenticated task/audience, reject stale/duplicate sequence values, verify task/tenant/audience binding, and use message IDs/nonces with bounded replay retention. Where messages cross trust boundaries, authenticate the envelope cryptographically.

### P2 — Agent output remains data, but no typed provenance contract is enforced at the bus boundary

The orchestration code bounds dependency output and the agent loop frames tool output as untrusted evidence. The task event payload itself is generic JSON and has no mandatory trust classification, source task/agent, content digest, or provenance chain. A downstream consumer can lose the distinction between user input, tool output, retrieved documents, and specialist-generated conclusions.

**Required direction:** use typed payload schemas and provenance metadata. Preserve untrusted status across forwarding and aggregation. Never promote a message to policy/instructions merely because it came from another internal agent.

### P2 — Delivery/reconciliation semantics are workflow-specific, not a general message-bus contract

The durable task store provides task state transitions and event sequence, while the coordinator separately advances subtasks and reconciles task state. This supports workflow progress but does not define general message delivery semantics such as recipient acknowledgement, retry/dead-letter policy, deduplication key scope, ordering across tasks, outbox/inbox transactions, or poison-message handling.

**Required direction:** introduce an explicit outbox/inbox protocol for inter-agent messages, with transactional enqueue, recipient acknowledgement, idempotent consumption, bounded retries, dead-letter quarantine, and reconciliation metrics. Keep task orchestration as a consumer of that protocol rather than treating arbitrary task events as messages.

## Recommended secure message flow

1. Authenticated principal requests a task; control plane derives tenant and owner.
2. Coordinator selects an enabled specialist and issues a narrow, expiring task grant.
3. Sender creates a typed message envelope with sender/recipient, task, tenant, audience, message ID, sequence, expiry, grant reference, provenance, and payload digest.
4. Trusted transport authenticates the sender; receiver validates signature/transport identity, grant, tenant, recipient, expiry, replay state, and schema.
5. Receiver treats payload as untrusted data, never policy or executable instruction.
6. Acknowledgement and message consumption are recorded transactionally in an inbox/outbox journal.
7. Revocation, cancellation, and task expiry invalidate queued messages and grants.
8. Audit records metadata and digest, not private payload contents.

## Validation plan

Before production use as a multi-agent message bus, add and run:

- Sender spoofing tests: one agent cannot emit as another agent.
- Recipient/audience tests: a message for agent A is rejected by agent B.
- Cross-tenant message, task, event, and result isolation tests.
- Worker lease tests: non-owner cannot write worker progress/artifact/completion events.
- Replay tests for duplicate, stale, expired, and cross-task messages.
- Signature/key rotation and revocation tests.
- Poison-message, retry, acknowledgement, and dead-letter tests.
- Prompt-injection tests proving forwarded agent output remains untrusted.
- Multi-process and concurrent consumer tests against the production storage/transport topology.

## Limitations and test status

This audit is based on source inspection of the paths listed above. No live message-bus exploit was demonstrated, no production transport topology was inspected, and no tests were executed during this audit. Existing tests were inspected as evidence of intended controls; they are not represented as passing in this report.

No runtime patch was submitted. A safe fix requires an explicit sender/recipient/tenant/grant contract and transactional worker-lease enforcement across all event-writing paths. A local message-signing helper without identity lifecycle, key custody, and receiver enforcement would not establish secure agent communication.
