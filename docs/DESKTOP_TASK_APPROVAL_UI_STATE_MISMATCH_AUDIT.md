# Desktop task approval UI state mismatch audit

**Status:** confirmed source-level defect; remediation not included in this audit commit.

## Finding

The native desktop task-review interface only enables its approval action for tasks in `waiting_approval`, but the workflow store's actual approval protocol requires the task to remain in `queued`. The normal task creation and worker-claim paths do not transition an unapproved queued task into `waiting_approval`.

As a result, approval-required tasks created by the specialist planner are persisted as `queued`, shown in the desktop task list as `queued`, and cannot be approved through the desktop UI. Workers correctly refuse to claim them until approval is recorded, so this creates a dead-end for the normal desktop workflow.

## Evidence

1. `WorkflowRunStore.create()` inserts new workflow runs with state `queued`.
2. `SpecialistAgentCoordinator.advance()` creates specialist workflow tasks with `requires_approval` copied from the plan. The desktop model planner explicitly sets this field to `True`.
3. `WorkflowRunStore.claim_next()` selects only `queued` rows and skips approval-required rows unless they have valid, unexpired, digest-bound approval metadata and a non-revoked approval session. It does not move unapproved tasks to `waiting_approval`.
4. `WorkflowRunStore.approve_with_event()` explicitly accepts only rows whose state is `queued`; it rejects `waiting_approval`.
5. `ui/desktop/launch.py::_update_task_actions()` enables the approval button only when the task-list state equals `waiting_approval`.
6. `ui/desktop/launch.py::_review_and_approve_task()` likewise rejects any task whose state is not `waiting_approval`, before loading and reviewing the stored definition.
7. The task-list view is populated from `TaskRuntime.list_task_summaries()`, which returns the persisted workflow state unchanged. Thus a newly dispatched approval-required task is presented as `queued`, not `waiting_approval`.

The `waiting_approval` state exists in the workflow state enum and transition table, but the audited normal create/claim/approval path contains no transition that places a newly created unapproved task into that state.

## Impact

- Desktop users cannot complete the approval workflow for approval-required queued tasks through the intended UI.
- The worker correctly leaves these tasks unclaimed, so they remain queued indefinitely unless an alternate trusted caller invokes the approval API directly.
- This is an availability and workflow-integrity defect in the human approval control, not an approval bypass: the worker-side approval gate remains fail-closed.

## Recommended remediation

Choose one consistent state contract and cover it end to end:

**Preferred minimal change:** treat `queued` as the reviewable pre-approval state in the desktop UI. Enable the review action for queued tasks whose stored definition contains at least one approval-required step, and have the review handler re-fetch the task, verify its current version and queued state, display the exact persisted definition, then call `approve_task()` with that exact version. Do not make approval available for queued tasks whose definitions do not require it.

Alternatively, introduce an explicit, atomic transition into `waiting_approval` at creation or before review, and update the store approval method and worker claim protocol consistently. Avoid changing only the UI state predicate or only the store predicate.

## Required regression coverage

- A model-generated specialist plan produces a queued task with `requires_approval=True`.
- The desktop task list exposes review/approval for that queued task.
- The review dialog displays the exact stored definition and version.
- Approval succeeds only for the current queued version and current authenticated session.
- A concurrent task mutation makes the stale review fail and requires refresh.
- A task without approval-required steps does not expose the approval action.
- The worker cannot claim the task before approval and can claim it only after valid approval.
- Session revocation and approval expiry continue to prevent claim.

## Scope and limitations

This is a source inspection of PR #1346 at the audited head. No application was launched and no tests were executed. The finding is based on the desktop UI predicates, workflow-store state transitions, task creation, and worker claim conditions. The audit does not assert that the proposed remediation has been implemented.
