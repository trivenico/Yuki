# Yuki core component contracts

Status: architecture baseline under active implementation. This document is the canonical map for component ownership and integration order; it does not claim every listed component is production-ready.

## 1. Architecture rule

Build and verify the stable core before wiring optional integrations. Each capability must have one authoritative owner for its contract, persistence, authorization, execution, and audit behavior. User interfaces and integrations call the application layer; they do not bypass it.

Dependency direction:

```text
Interfaces (CLI / API / desktop / voice)
                |
                v
Application composition and lifecycle
                |
        +-------+--------+
        |                |
        v                v
Conversation / task   Capability catalog
application services  and planning
        |                |
        +-------+--------+
                |
                v
Policy + approvals + guarded tool protocol
                |
                v
Provider adapters / tool adapters / workers
                |
                v
Durable stores + audit events
```

Cross-cutting rules apply at every boundary: authenticated owner identity, least privilege, bounded inputs and outputs, deadlines, cancellation, idempotency, explicit side-effect classification, structured errors, audit evidence, and deterministic cleanup.

## 2. Canonical component ownership

| Component | Authoritative modules | Contract / responsibility | Required verification |
|---|---|---|---|
| Application composition | `core/conversation_application_runtime.py`, `api/runtime.py` | Construct one shared service graph; own startup/shutdown; inject owner-scoped dependencies | Runtime wiring, lifecycle and close-once tests |
| Model/provider boundary | `providers/base.py`, `providers/factory.py`, `providers/capabilities.py`, `core/agent_execution_loop.py` | Normalize provider capabilities, timeouts, cancellation, structured responses and errors | Provider contract suite, mock adapter suite, real-provider opt-in smoke |
| Agent execution | `core/agent_execution_loop.py`, `core/structured_tool_protocol.py` | Bounded model turns and tool dispatch; model proposals never authorize execution | Turn/tool budgets, malformed output, cancellation, prompt-injection and dispatch tests |
| Authorization and approvals | `core/permissions.py`, `core/guarded_tool_runtime.py`, `core/action_approvals.py`, `automation/workflow_approval.py` | One policy decision before each side effect; bind approval to principal, action and payload digest | Owner isolation, replay, expiry, tamper and revocation tests |
| Conversation persistence | `core/conversation_store.py`, `core/conversation_store_factory.py`, `core/conversation_service.py` | Owner-scoped history, optimistic versions, durable generation leases, execution journal and provider evidence | SQLite/Postgres contract parity, migration, concurrency and crash tests |
| Recovery and reconciliation | `core/conversation_recovery.py`, `core/provider_outcome.py` and reconciliation adapters | Recover interrupted work without blind side-effect replay; reconcile only through read-only provider evidence | Process-crash, lease-loss, duplicate-evidence and uncertain-outcome tests |
| Task/workflow persistence | `automation/workflow_store.py`, `automation/task_runtime.py`, `core/task_lifecycle.py` | Durable task state, versioned transitions, leases, progress events and idempotent enqueue | State-machine, lease, retry, cancellation and restart tests |
| Specialist orchestration | `automation/agent_coordinator.py`, `automation/specialist_worker.py`, `automation/worker_supervisor.py` | Decompose bounded plans and dispatch only approved, capability-matched work | Dependency ordering, budgets, isolation, worker crash and duplicate-dispatch tests |
| Memory and knowledge | `core/vector_memory.py`, `knowledge/` | Consent-aware storage, retrieval, provenance, retention and deletion | Tenant/owner isolation, retrieval quality, export/delete and prompt-injection tests |
| Integrations and tools | `integrations/`, `tools/`, `skills/`, `core/mcp_runtime.py` | Thin adapters implementing declared schemas; external content is untrusted; no integration grants itself permission | Adapter contract, secret redaction, network policy and approval tests |
| Desktop / voice / companion | `companion/`, `voice/`, `ui/` | Present state and collect user intent; invoke shared application services; visible capture/action status | Device lifecycle, permission UX, accessibility and end-to-end tests |
| Self-upgrade and learning | `automation/self_upgrade_pipeline.py`, `core/self_improvement.py`, `core/self_healing.py` | Propose isolated changes, run checks, request human approval, stage, health-check and rollback | Adversarial patch, branch isolation, approval, rollback and provenance tests |
| Operations and readiness | `core/readiness.py`, `core/doctor.py`, `scripts/`, `.github/workflows/` | Report verified capability evidence, dependency health and release readiness | Clean-install, cross-platform CI, security scan and reproducible release checks |

The capability manifest in `core/capability_manifest.py` remains the evidence-backed source for user-facing capability status. This architecture map does not override its status labels.

## 3. Stable contracts before integration

Every new or refactored component must declare:

1. **Owner:** the single module responsible for the contract and state transitions.
2. **Inputs/outputs:** typed schema, size limits, validation and versioning behavior.
3. **Identity:** authenticated principal and tenant/owner scope where applicable.
4. **Permissions:** read/write/execute scopes and approval requirements.
5. **Side effects:** exact external or persistent effects; classify reversible and irreversible actions.
6. **Reliability:** timeout, cancellation, retry budget, idempotency key and lease/fencing behavior.
7. **Failure behavior:** normalized error codes, partial completion state and recovery instructions.
8. **Observability:** redacted audit event, correlation ID and measurable health signals.
9. **Lifecycle:** startup, shutdown, resource ownership and deterministic cleanup.
10. **Evidence:** focused unit tests, integration tests, security regressions and platform coverage.

Avoid creating a second abstraction when an existing authoritative contract already covers the behavior. Migrate consumers to the canonical interface before deleting compatibility code.

## 4. Implementation order

### Foundation A — contracts and composition
- [x] Inventory existing core components and assign authoritative ownership.
- [ ] Stabilize provider/model request and response contracts.
- [ ] Stabilize tool schema, permission, approval and execution result contracts.
- [ ] Stabilize durable task and conversation event contracts.
- [ ] Make API, CLI and desktop compose the same application services.

### Foundation B — durable execution
- [ ] Pass conversation lease, execution journal, idempotency and recovery suites on Linux, Windows and macOS.
- [ ] Verify SQLite and Postgres implement the same repository behavior.
- [ ] Add explicit schema migration/version policy and backup/restore tests.
- [ ] Prove no automatic replay of uncertain side effects.

### Foundation C — agent runtime
- [ ] Connect bounded plans, typed steps, deadlines, budgets and cancellation.
- [ ] Require per-action authorization immediately before execution.
- [ ] Add isolated workspaces for coding and file modification.
- [ ] Make specialist workers use the same policy, journal and audit services.

### Foundation D — user-facing product
- [ ] Complete one shared application API for CLI, desktop and web.
- [ ] Add task dashboard, approvals, audit history, diagnostics and settings.
- [ ] Add voice and desktop observation/control behind explicit user permissions.
- [ ] Validate portable runtime on clean Windows and Linux machines.

### Foundation E — business workflows
- [ ] Add email, calendar, CRM, document and finance adapters one at a time.
- [ ] Start with read-only and draft workflows; require confirmation before sending, publishing, purchasing or changing records.
- [ ] Test end-to-end workflows with sandbox accounts and synthetic data.
- [ ] Measure completion, latency, cost, error recovery and human intervention rate.

## 5. Definition of done for each component

A component is not complete because a Python file exists or a unit test passes. It is complete only when:

- its interface and ownership are documented;
- focused unit and contract tests pass;
- integration and failure-mode tests pass;
- security and permission boundaries are verified;
- Linux, Windows and macOS behavior is covered where supported;
- startup/shutdown and cleanup are deterministic;
- the capability manifest reflects the evidence and limitations;
- setup, operations and recovery instructions are current.

## 6. Safe evolution loop

Yuki's self-evolution path is supervised and reproducible:

`observe -> diagnose -> propose -> isolated branch/worktree -> test -> security review -> human approval -> staged rollout -> health check -> rollback if unhealthy`

No component may rewrite production code, expand its own permissions, deploy, or perform consequential business actions merely because a model proposed it. Approval is bound to the exact reviewed change and expires when that change changes.
