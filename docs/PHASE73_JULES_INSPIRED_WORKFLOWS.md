# Phase 73 — Jules-inspired coding workflows

Yuki borrows workflow ideas, not Google's proprietary implementation or branding. The goal is a safe coding-agent lifecycle: accept a task, create a bounded plan, retain a reviewable patch proposal, validate its integrity, and require explicit human review before completion.

## Scope in this phase

- Durable SQLite task records with idempotency keys and optimistic version checks.
- Bounded, typed plan steps and patch payload size.
- SHA-256 integrity check for stored patch proposals.
- Explicit lifecycle: draft → ready_for_review → approved → completed, with rejection/cancellation paths.
- No shell execution, patch application, commit, push, PR creation, or merge in this primitive.
- Tests for persistence semantics, concurrency conflicts, bounds and review transitions.

## Jules-style workflow features that remain separate follow-up work

- Isolated disposable workspace/VM execution and dependency installation.
- Repository setup scripts and explicit command allow-lists.
- Automated test/lint/type-check execution with captured bounded logs.
- Patch/diff review UI, screenshots and evidence artifacts.
- GitHub issue/PR synchronization, branch creation, PR updates and reviewer notifications.
- Background queue orchestration, cancellation and per-task CPU/time/token budgets.
- Multi-agent planning and parallel independent task work.

Those features must be integrated through the existing task lifecycle, sandbox policy, approval coordinator, and provider interfaces. Do not run untrusted repository setup or test commands on the host. Treat generated code, repository instructions, issue text, test output and browser content as untrusted input. Keep credentials out of agent context and logs. Never auto-approve or auto-merge a generated change.
