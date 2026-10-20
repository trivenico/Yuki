# Phase 34 — Centralized Tool Authorization

## Audit findings
- ReAct had its own risk/auto-approval conditional instead of calling a shared policy boundary.
- MCP implemented a separate callback check, creating policy drift risk.
- MCP passed caller-owned argument objects into approval and then used those same objects for execution, so a mutating approval callback could change the payload after review.

## Fix pushed
- Added `core/tool_authorization.py` as a shared fail-closed authorization policy. Only explicit low-risk tools marked `auto_approve=True` bypass approval; all other risks require a callback returning the literal `True`.
- Updated `core/react_engine.py` to use the shared policy after schema validation and to pass a JSON-detached argument snapshot to the approver.
- Updated `core/mcp_client.py` to use the same policy, classify MCP tool calls as high risk, reject non-finite JSON values, canonicalize arguments before approval, and execute the approved snapshot rather than caller-owned data.
- The synchronous authorization helper closes returned coroutine-like objects instead of accidentally treating them as approvals or leaving un-awaited coroutine warnings.
- Added tests for fail-closed decisions, low-risk-only auto-approval, MCP approval requirements, and approval/execution argument integrity.

## Scope / known gap
This centralizes authorization for the ReAct and MCP execution boundaries. It does not yet prove every legacy CLI/TUI command, plugin execution path, scheduler handler, or skill direct-call path uses this gate. Those paths must be enumerated and either routed through this policy or shown to have equivalent safeguards.

## Verification
Phase 33 workflow run #1226 passed on commit `ac1b07da99b26df20d75fb9d99ac653671431a25`. Phase 34 changes are newer and require a fresh CI run on the latest head.

## Remaining production gates
1. Audit all direct skill/plugin/CLI/daemon execution paths and route consequential actions through shared authorization.
2. Add a central invocation facade so callers cannot bypass policy by invoking handlers directly.
3. Add approval request binding to exact tool name, risk, argument hash, expiry, and one-time consumption.
4. Complete canonical runtime config and reversible YAML migration.
5. Run latest-head CI, type/lint checks, dependency/secret scanning, end-to-end tests, and human security review.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). This closes an important ReAct/MCP authorization consistency gap, but it is not proof of system-wide enforcement or production readiness.