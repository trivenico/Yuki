# Yuki Agent Company: CEO and specialist workforce

## Product vision

Yuki is the user's personal companion and earning coach, and also the CEO/orchestrator of a virtual IT company. It receives goals, decomposes them into bounded tasks, assigns designated roles, coordinates specialist results, reviews evidence, and reports back to the user.

## Important terminology

- **Role profile:** a job description and mission in the organization chart.
- **Agent instance:** a running model-backed worker with a provider, context, resource budget, and lifecycle.
- **Worker handler:** an explicitly registered function that can execute one bounded task.
- **Elite council:** ten cross-functional reviewers for architecture, engineering, security, reliability, product, research, AI/data, earning/growth, quality, and privacy.
- **Task:** a tracked unit of work with an owner role, risk, approval requirement, status, and result summary.

A catalog of 120 roles is not 120 running AI models. Running 100+ model workers continuously would be expensive, difficult to supervise, and unnecessary for most workloads. Yuki should create agent instances on demand, cap concurrency and budgets, and start with a small verified pool. The organization chart scales the role taxonomy without pretending those agents are online.

## Phase 9 implementation

- `core/agent_org.py` defines 120 role profiles across 30 departments and exactly ten elite council roles.
- The in-memory dispatcher executes only handlers explicitly registered by trusted application code.
- External, financial, device-control, and local-write tasks require an approver returning exactly `True`; absent or failing approval blocks the task.
- Unknown roles, task overflow, invalid risk values, missing handlers, and handler failures are handled conservatively.
- This layer does not create subprocesses, call a model, run shell commands, contact external services, or grant tool permissions.

## How Yuki should become CEO

1. User gives Yuki a goal and constraints.
2. Planner proposes a task graph with role, inputs, expected artifact, risk, deadline, and budget.
3. Yuki validates each task against the capability registry and policy.
4. Low-risk, read-only work can be delegated to a verified handler; all state-changing/external work passes through the central permission gate.
5. Specialist workers return structured results with sources, tests, assumptions, and uncertainty.
6. The ten-person virtual elite council reviews high-impact work using independent checklists; it is not a promise of ten separate model calls.
7. Yuki reconciles conflicts, requests user decisions when needed, and presents a single executive summary.
8. Every task records lifecycle state, cost/time budget, and trace ID once the durable task store is implemented.

## Next production work

- Replace the in-memory task store with SQLite-backed or service-backed durable tasks, migrations, leases, idempotency keys, cancellation, and restart recovery.
- Add a planner that emits validated task graphs; do not allow free-form model text to trigger handlers.
- Integrate the existing `PermissionGate` with short-lived, action-bound approvals and correlated audit events.
- Run workers in OS-enforced sandboxes with CPU/memory/time limits and filesystem/network policies.
- Add a provider-neutral worker interface, per-agent model budgets, concurrency caps, cancellation, retries, and cost telemetry.
- Add an independent reviewer pipeline and quality thresholds before marking tasks complete.
- Add CI for lint, type checks, security scanning, coverage, and integration tests.

## Production acceptance

No production launch until tests pass in CI, high-risk tool isolation is verified, permissions fail closed, secret redaction is tested, task recovery works after restart, and monitoring/rollback procedures have been exercised. A virtual role catalog alone does not constitute a production multi-agent system.
