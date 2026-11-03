# Agent37 platform assessment for Yuki

**Review date:** 2026-11-03  
**Reference:** https://www.agent37.com/  
**Related pages:** https://www.agent37.com/cloud and https://www.agent37.com/personal

## Executive assessment

Agent37's strongest product idea is not a novel model or a large collection of agents. It is the operational substrate around an agent: a persistent, isolated computer; a stable API to provision and message it; durable files and credentials; scheduled/background execution; live operational visibility; integrations; and transparent resource billing.

Yuki already has substantial foundations for several of these capabilities. The correct strategy is to complete and integrate those foundations rather than build a parallel task engine or copy Agent37's product surface prematurely.

This is a public-product feature/architecture review, not an independent validation of Agent37's security, isolation, pricing, availability, or compliance claims. Vendor claims such as gVisor isolation, encryption, no prompt collection, and SOC 2 status must be verified independently before relying on them.

## Agent37 capabilities observed

The reviewed Agent37 pages describe:

- Per-customer persistent sandboxes with separate files, tools, and credentials.
- Ready-made agent harnesses and custom harness support.
- An API for creating and messaging agent instances.
- Persistent files across sessions, sleep, and restart, with backups.
- Lifecycle controls including start, stop, restart, resize, sleep, and wake.
- Browser, terminal, file-browser, and desktop access.
- Scheduled jobs and unattended execution.
- Server-sent event streaming for text, reasoning/activity, and tool events.
- Model gateway or bring-your-own-key options.
- Third-party integrations and paid tools.
- Resource and log observability, plus metered compute/storage pricing.
- Security claims including workload isolation, network boundaries, and encryption.

Sources: Agent37 homepage, Cloud page, and Personal page (URLs above; reviewed 2026-11-03).

## Existing Yuki foundations verified on main

| Capability | Existing implementation | Actual boundary |
|---|---|---|
| Durable task state | `core/durable_task_engine.py`, `automation/task_runtime.py`, workflow store | Durable local task records, leases, idempotency and events; SQLite is not a distributed queue. |
| Specialist agents | `automation/agent_coordinator.py`, specialist worker modules | Static capability registry and bounded dependency orchestration; not arbitrary dynamic agent loading. |
| Worker supervision | `automation/worker_supervisor.py` | Explicit finite cycles; no always-on scheduler or implicit replay. |
| Agent lifecycle checkpoints | `automation/agent_task_journal.py` | Safe metadata checkpoints and lease/version checks; not full model/tool transcript replay or exactly-once side effects. |
| Sandbox policy | `core/sandbox_policy.py` | Validates capability and resource policy; not itself an OS isolation boundary. |
| Docker command construction | `core/sandbox_runtime.py` | Builds fail-closed Docker argv; allowlisted networking is deliberately unsupported without an egress gateway. |
| Approved coding execution | `core/coding_task_runner.py` | Executes an approved bounded Docker command with no host shell, resource/output limits and cleanup confirmation. |
| Authenticated application runtime | `api/runtime.py` and API/auth modules | Authenticated conversation composition; not yet a tenant-isolated sandbox provisioning API. |

Important distinction: Yuki already has a Docker-backed coding-task runner, so it would be inaccurate to describe all sandbox execution as missing. What is missing is the productized persistent per-agent sandbox control plane and its secure lifecycle/tenant integration.

The current Docker runner is not equivalent to Agent37's claimed gVisor isolation. Docker flags alone do not establish a hardened multi-tenant boundary. Do not expose arbitrary customer workloads to it without a separate threat model, hardened runtime, host isolation, and independent security acceptance.

## Recommended Yuki implementation

### P0 — Finish Phase 2 safely

Complete the current approval/audit work before wiring long-lived execution to it:

- Make approval decisions and task state transitions race-safe.
- Verify crash recovery at every audit/state boundary.
- Bind actions to authenticated principals and tenant/workspace ownership.
- Preserve the no-automatic-replay rule for ambiguous external side effects.
- Establish audit retention, backup, restore, and access controls.

A persistent sandbox multiplies the impact of weak identity or approval semantics, so it must not bypass these gates.

### P1 — Integrate the existing durable agent runtime

Use existing task/runtime components as the source of truth:

- A stable agent-instance ID mapped to an owner/tenant and a workspace.
- Task queue and worker leases remain authoritative for execution.
- Persist lifecycle events and safe checkpoints; keep prompts, secrets, and raw tool outputs out of operational logs by default.
- Add explicit instance states: provisioning, ready, sleeping, waking, stopping, stopped, failed, deleting.
- Implement idempotent lifecycle operations and bounded retries for infrastructure actions.
- Add explicit operator/user controls for pause, resume, cancel, restart, and delete.

Do not create a second task journal or another competing workflow engine.

### P2 — Hardened execution backend

Evolve the existing runner behind a backend interface:

- Local Docker backend for developer-only use.
- A hardened production backend (for example, gVisor or a microVM runtime) only after deployment-specific threat modelling and validation.
- Immutable image digests, non-root identity, read-only root filesystem, dropped capabilities, seccomp/AppArmor or equivalent, CPU/memory/PID/disk quotas, bounded output, and forced cleanup.
- Deny network by default. Allowlisted egress must be enforced by a separate gateway/proxy; never treat an application-level hostname list as network isolation.
- Never mount the Docker socket, host home, SSH material, cloud credentials, or unrelated host paths into an agent workspace.

### P3 — Persistent workspace and backup

- Separate code/configuration from mutable workspace state and secrets.
- Per-tenant/per-agent filesystem boundaries and restrictive ownership/permissions.
- Snapshot/backup with versioning, encryption, restore drills, retention and deletion verification.
- Portable Yuki repository clone remains the source for code; workspace backup is a separate encrypted artifact. Never commit user memory, customer files, runtime databases, logs, or credentials into Git.
- Define data export and deletion semantics before offering customer workspaces.

### P4 — Agent instance API and operations UI

Add authenticated, tenant-scoped endpoints only after the identity model is established:

- Create/get/list agent instances.
- Start/stop/restart/pause/resume.
- Read status and bounded logs/metrics.
- Upload/download workspace artifacts with authorization and size limits.
- Stream lifecycle/task events.
- Enforce per-tenant quotas and rate limits.

The UI should expose task board, logs, terminal and file access only through short-lived, scoped capabilities. A signed URL must be narrowly scoped, expiring, revocable where possible, and never act as a general-purpose bearer credential.

### P5 — Scheduling, integrations, and economics

- Use a durable scheduler that enqueues work into the existing task runtime; scheduler ticks must be idempotent and timezone/DST aware.
- Integrations must use least-privilege OAuth grants or scoped tokens, per-tenant isolation, revocation, and auditable access.
- Add per-agent token/tool/compute/storage budgets and hard stop limits before any metered external service.
- Show estimated and actual usage; never silently spend from a shared balance.

## Adoption decisions

**Adopt the patterns:** persistent agent workspaces, a clean provisioning API, explicit lifecycle state, event streaming, scheduled tasks, resource visibility, per-agent budgets, and portable export/backup.

**Adapt carefully:** per-customer sandboxing, desktop/browser access, BYOK and integrations. These require strong tenant identity, secret isolation, revocation, network policy, and consent.

**Do not copy blindly:** claims of low cost, a specific provider's security/compliance posture, arbitrary always-on execution, broad outbound internet access, or a huge integration catalogue before Yuki has operational controls and support capacity.

## Phase/serial discipline

This assessment does not create or skip a phase number. The repository already contains earlier work in Phases 69, 70, 74, 83, 84, and 142. Their delivered scope and remaining gaps must be reconciled against the phase ledger. Complete Phase 2 and reconcile the historical serial ledger before declaring a new numbered phase for persistent agent hosting. Phase 1 remains deferred by owner direction; its outstanding review and acceptance gates are not waived.

## Acceptance criteria before production claims

- Tenant-isolation tests prove one tenant cannot read, list, mount, or access another tenant's workspace or credentials.
- Runtime escape/privilege, network egress, resource exhaustion, filesystem, and cleanup tests pass on the actual production backend.
- Kill/restart tests cover host process, worker, container, database and scheduler failures.
- Backup restore and deletion verification are demonstrated.
- API authorization, event streaming, file transfer and signed access links pass security tests.
- Budget enforcement is tested under concurrency and failure.
- Independent security review and deployment-specific operational runbooks are complete.
