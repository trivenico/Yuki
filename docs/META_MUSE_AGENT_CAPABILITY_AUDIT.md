# Meta Muse agent capability audit and Yuki implementation plan

**Audit date:** 2026-10-26
**Correct target:** Meta Muse at https://muse.ai/ — the personal AI agent. This document intentionally does not audit the former muse.ai video-hosting service / Skiv.
**Purpose:** Translate public Muse product patterns into a safe, incremental roadmap for Yuki; this is not a claim that these features are already implemented.

## Capability inventory

Public Muse materials describe an agent that can work across apps and a cloud computer, pursue longer-running goals, browse the web, build custom tools, create interactive artifacts, maintain useful memory/context, and perform actions with safety/approval boundaries. Product features and availability can evolve; verify each item against current official materials before promising parity.

Official references:
- Muse home: https://muse.ai/
- Product introduction: https://introducing.muse.ai/
- Safety design: https://introducing.muse.ai/how-we-built-safety-into-muse
- Platform docs: https://platform.muse.ai/docs
- Business product: https://muse.ai/business

## Capability map for Yuki

| Muse pattern | Yuki target | Implementation | Safety and acceptance |
|---|---|---|---|
| Cloud computer and browser execution | Disposable computer/browser worker | Job API, isolated worker, action traces, bounded time/resources, artifact collection | No production secrets in worker; explicit domain/action policies; cleanup verified; isolation integration-tested |
| Long-running goals | Durable goal/task orchestration | Goal record, task graph, dependencies, resumable state, retry budget, checkpoints, cancellation, pause/resume | Idempotent steps; deadlines and spend limits; approval checkpoints for external effects |
| Proactive work and follow-up | Scheduled/condition-based runs | Scheduler, event triggers, quiet hours, user scope, notifications and run history | Explicit opt-in, pause/revoke, rate limits, duplicate suppression; no hidden outbound actions |
| Tool creation and custom workflows | Versioned skill/tool builder | Typed manifest, schemas, permission declarations, pinned dependencies, linting, tests, isolated trial run | Generated code is untrusted; no automatic promotion; review, CI, approval before activation |
| Persistent memory and context | User-controlled memory store | Typed facts, provenance, confidence, TTL, project scopes, search, export/import, deletion | Tenant boundaries, secret filtering, retention, correction and forget controls, audit |
| Separate workspaces/conversations | Project/task contexts | Context bundles, per-task history, workspace selection, artifact references, summaries | Explicit context boundaries and least privilege; prevent cross-project leakage |
| Interactive artifacts | Durable deliverable system | Files, reports, plans, previews, structured outputs, revision history and links | Safe rendering, content limits, provenance, permissions, versioning |
| Connectors/integrations | Scoped app connectors | Connector registry, OAuth/token handling, read/write scopes, health, rate limits, disconnect/revoke | Read-only default; per-action authorization; secret store; redacted logs |
| Approval and safety UX | Central policy/approval service | Human-readable plan, exact resources/changes, risk tier, one-time approval bound to user/session/job/arguments, expiry | Fail closed; re-check before execution; single-use non-transferable approvals; durable audit |
| Status and observability | Run dashboard | Current step, tool calls, costs, elapsed time, retries, approvals, logs, outputs and failure reason | Redaction, access control, retention limits, tamper-evident events where appropriate |
| Recovery and self-improvement | Supervised upgrade pipeline | Branch/worktree, patch proposal, tests, diff summary, rollback artifact | No self-merge/self-deploy; protected branch, independent review, isolated tests, explicit promotion gates |

## Implementation order

### M0 — reliability and provider readiness
1. Complete the real-provider PR and get the complete cross-platform CI suite green.
2. Add provider readiness/status, request timeout, bounded response size, normalized errors, safe transient retry policy, and secret redaction.
3. Add offline deterministic tests; separate no-network tests from explicitly opt-in live provider smoke tests.
4. Verify credentials locally without committing them; report unsupported/unavailable providers honestly.

### M1 — durable agent run engine
1. Define versioned Goal, Task, TaskAttempt, ApprovalRequest, RunEvent, and Artifact schemas.
2. Persist state transactionally; support restart recovery, lease ownership, idempotency keys, cancellation, pause/resume, deadlines and retry budgets.
3. Add a deterministic planner/executor boundary; each step has typed inputs/outputs and declared capabilities.
4. Test crashes at every transition, duplicate delivery, stale lease, retry exhaustion, cancellation, and partial side effects.

### M2 — secure tools and isolated computer worker
1. Route browser, shell, and generated-code execution only through a dedicated worker, never inside the privileged orchestrator.
2. Use an immutable image digest, rootless container or equivalent OS sandbox, read-only base filesystem, no network by default, bounded temporary storage/CPU/memory/PIDs, non-root identity and verified cleanup.
3. Grant scoped network access only per job when needed; prevent cloud metadata and private-network access unless explicitly authorized.
4. Capture structured action traces and artifacts; require approvals for sends, purchases, deployments, permission changes and other irreversible actions.
5. Prove isolation through a protected-runner acceptance test, not just unit mocks.

### M3 — memory and project context
1. Store source-linked memories with workspace scope, provenance, confidence, expiry and sensitivity classification.
2. Add user inspection, correction, export, retention and deletion workflows.
3. Retrieve only memories authorized for the active project; never treat memory or document content as higher-priority policy.
4. Evaluate retrieval quality, stale-memory behavior and cross-workspace leakage.

### M4 — skill and workflow builder
1. Define a signed/versioned skill manifest with schemas, capabilities, dependencies, limits, tests and declared side effects.
2. Generate skills as proposals, run static checks and tests in isolation, show a diff and require review.
3. Activate only an approved immutable version; support disable/rollback and provenance.
4. Test malicious instructions, dependency confusion, secret access, network exfiltration and privilege escalation.

### M5 — connectors and proactive runs
1. Implement one connector at a time, read-only by default.
2. Use least-privilege OAuth scopes, token rotation/revocation, rate limits and audit.
3. Add scheduled/triggered runs with explicit consent, per-run budget, quiet hours, duplicate suppression and kill switch.
4. Keep external side effects behind policy and approval checks.

### M6 — interactive artifacts and evaluation
1. Create versioned deliverables with safe previews, structured schemas and source references.
2. Add golden task sets, adversarial prompt-injection cases, tool-call correctness, completion, latency, cost and regression metrics.
3. Track run-level outcomes and allow reproducible replay using sanitized fixtures.
4. Never claim production readiness based solely on unit tests or mocked runs.

## Cross-cutting design rules
- Treat user content, webpages, emails, files, model outputs and memories as untrusted data; embedded instructions cannot override policy.
- Separate planning from authorization. The model may propose an action; deterministic policy and user consent authorize it.
- Re-check authorization immediately before each side effect and bind approvals to the exact operation.
- Never put credentials in prompts, logs, artifacts, generated skill bundles or worker images.
- Default to least privilege, no network, no external writes and no autonomous production deployment.
- Use feature flags and kill switches; every autonomous loop needs a deadline and resource budget.
- Make failures observable and recoverable; never hide provider errors by fabricating success.

## Immediate next work items
1. Fix remaining PR #341 tests against the latest head and wait for every CI job.
2. Add provider error/timeout and safe-retry tests, without a live API key.
3. Create a separate implementation PR for M1 durable goal/task/run records and crash-recovery tests, reusing existing Yuki workflow primitives.
4. Keep self-upgrade promotion disabled until rootless isolation acceptance and independent security review are complete.

## Completion criteria
A capability is implemented only when it has production code behind a clear interface, unit/integration and authorization/failure-path tests, documented configuration and limits, observable outcomes, a disable/rollback path, CI evidence on the exact commit, and a real integration acceptance test where external infrastructure is required.

This plan is a roadmap, not proof that Yuki already provides full Muse parity.