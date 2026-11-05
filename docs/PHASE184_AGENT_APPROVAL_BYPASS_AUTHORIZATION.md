# Phase 184 — Specialist-agent approval-bypass authorization

## Security change

Creating a specialist-agent plan and creating a task that does not require human approval are now separate privileges. Any plan containing a subtask with `requires_approval=False` must pass the authorization callback for `agent:plan_without_approval` in addition to ordinary `agent:plan` authorization.

## Default-safe behavior

- Ordinary subtasks retain `requires_approval=True` by default.
- A caller with plan-creation permission alone cannot remove the approval gate.
- The coordinator does not grant the new privilege; the host application's trusted authorization policy must explicitly grant it.
- This does not make specialist agents trusted or sandboxed. Workers still execute through the existing workflow runtime and its policy checks.

## Validation

Regression coverage verifies that ordinary plan creation remains available while approval bypass is rejected without the separate authorization grant. Existing tests that intentionally exercise no-approval workflows grant the additional permission explicitly.

## Deployment note

Before enabling this feature in production, update the host authorization policy to recognize `agent:plan_without_approval`. Until explicitly granted, approval-bypass plans fail closed.
