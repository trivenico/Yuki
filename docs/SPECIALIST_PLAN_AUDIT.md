# Specialist plan audit trail

Specialist plans maintain an owner-scoped append-only event sequence in SQLite.

## Events currently recorded

- `plan.created`: records the number of subtasks, not descriptions or prompts.
- `subtask.dispatched`: records subtask ID, selected specialist ID and task ID.
- `plan.cancelled`: records the number of subtasks in the cancellation request.

Every event includes a per-plan monotonic sequence, actor ID and UTC timestamp. Reads require the normal `agent:read` authorization callback and an exact persisted owner match. Reads are paginated with a maximum of 100 events per call.

## Data minimization

The audit payload intentionally excludes task descriptions, model prompts, retrieved memory, specialist outputs, credentials and provider responses. Task execution has its own task event journal; these plan events are only the orchestration metadata needed to explain delegation.

## Reliability boundaries

Plan creation and its initial audit event are written in one SQLite transaction. Dispatch state and its audit event currently use separate transactions because the task runtime and coordinator may share the same database. A process crash between those operations can leave a dispatched task without the corresponding plan event; reconciliation must compare the plan subtask record with the authoritative task runtime. This is not yet a tamper-evident or externally anchored audit log.

Before public multi-user deployment, add atomic cross-journal reconciliation or a transactional outbox, actor attribution for all plan/subtask state transitions, retention/export controls, and independent review of audit access and integrity.
