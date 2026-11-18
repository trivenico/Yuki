# User interface

The current supported interface is the guarded terminal entry point in `main.py` and the same-origin authenticated web chat shell in `ui/web`.

## Workforce dashboard API

The optional `AuthenticatedWorkforceDashboardAPI` can be mounted by passing a configured `WorkforceDashboard` to `build_conversation_runtime(..., workforce_dashboard=...)`. It reuses the existing bearer-session authentication and permits only `admin` and `operator` roles.

Read-only endpoints:

- `GET /v1/workforce/dashboard?max_goals=100` — workforce, task, department, and bounded goal snapshot.
- `GET /v1/workforce/employees?page=1&page_size=25` — paginated employee directory; optional `department`, `status`, and `search` filters.
- `GET /v1/workforce/departments?name=Engineering` — department summary and roster.

The dashboard dependency is deliberately injected. Deployments must pass the same registry and durable task engine used by their workforce runtime; this API does not create a second in-memory workforce or queue. The dashboard API does not expose task mutation, approvals, or execution controls.

## Remaining approval workflow work

The existing web shell now includes a read-only Executive Command Center, goal execution timeline, and optional CEO Approval Center inbox/audit view. The graphical dashboard does not yet submit approval decisions. Future action controls must use the existing `ApprovalCoordinator`, creator actor/session binding, explicit action confirmation, one-time pending-action consumption, audit events, and guarded execution/recovery path. Never bypass `core.permissions.PermissionGate`.

A graphical approval decision workflow and Textual TUI are not implemented yet. Before enabling approval buttons, show the exact action, target/workspace, arguments, risk and consequences; require an explicit per-action confirmation; preserve creator/session restrictions; and make uncertain outcomes visible for recovery rather than offering automatic retries.

## Executive workforce command center

The authenticated web shell includes an optional read-only Executive Command Center for admin/operator sessions when the workforce dashboard API is mounted. It displays workforce and task metrics, department summaries, a searchable/filterable paginated employee directory, and recent persisted goals. It reuses the existing same-origin bearer session, calls only the read-only `/v1/workforce/*` endpoints, and renders API values with DOM text nodes. No execution, approval, or permission-management controls are exposed. Data refresh is manual; this is not a push/live-update stream.

Goal cards open a bounded execution timeline with task assignments and paginated event envelopes. Arbitrary low-level event detail is deliberately omitted from the API projection to avoid exposing sensitive executor messages.

## CEO approval center (read-only increment)

Deployments may optionally pass `pending_approvals` and `approval_audit` to `build_conversation_runtime` alongside `workforce_dashboard`. The authenticated admin/operator API then exposes:

- `GET /v1/workforce/approvals?limit=100` — safe pending metadata for the authenticated action owner only (reference, skill identifier, expiry); action arguments and action snapshots are never returned.
- `GET /v1/workforce/approvals/audit?limit=50` — metadata-only audit events with hashed actor/pending identifiers and a hash-chain integrity result.

The web command center displays this inbox and audit history. This increment deliberately has no approve/deny buttons and cannot execute actions. Approval actions must continue through the existing `ApprovalCoordinator`, trusted actor/session binding, explicit confirmation, one-time consumption, and audited execution/recovery path. These services are optional and must be the same persistent stores used by the guarded runtime; the API does not create duplicate stores.
