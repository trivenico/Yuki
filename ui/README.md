# User interface

The current supported interface is the guarded terminal entry point in `main.py` and the same-origin authenticated web chat shell in `ui/web`.

## Workforce dashboard API

The optional `AuthenticatedWorkforceDashboardAPI` can be mounted by passing a configured `WorkforceDashboard` to `build_conversation_runtime(..., workforce_dashboard=...)`. It reuses the existing bearer-session authentication and permits only `admin` and `operator` roles.

Read-only endpoints:

- `GET /v1/workforce/dashboard?max_goals=100` — workforce, task, department, and bounded goal snapshot.
- `GET /v1/workforce/employees?page=1&page_size=25` — paginated employee directory; optional `department`, `status`, and `search` filters.
- `GET /v1/workforce/departments?name=Engineering` — department summary and roster.

The dashboard dependency is deliberately injected. Deployments must pass the same registry and durable task engine used by their workforce runtime; this API does not create a second in-memory workforce or queue. The dashboard API does not expose task mutation, approvals, or execution controls.

## Remaining graphical work

The executive command-center frontend is not implemented yet. The current web shell remains the chat interface. Future UI work should integrate with the authenticated endpoints above and display workforce status, employee/department views, task progress, and approval state. Any action controls must continue to use the existing permission and approval gates.

A graphical dashboard and Textual TUI are not implemented yet. Future UI work should display the exact action, target/workspace, risk, and one-time approval decision; provide cancellation and an audit view; and never bypass `core.permissions.PermissionGate`.

## Executive workforce command center

The authenticated web shell includes an optional read-only Executive Command Center for admin/operator sessions when the workforce dashboard API is mounted. It displays workforce and task metrics, department summaries, a searchable/filterable paginated employee directory, and recent persisted goals. It reuses the existing same-origin bearer session, calls only the read-only `/v1/workforce/*` endpoints, and renders API values with DOM text nodes. No execution, approval, or permission-management controls are exposed. Data refresh is manual; this is not a push/live-update stream.
