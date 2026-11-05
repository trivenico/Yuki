# Phase 191 — Agent plan recovery

The coordinator now exposes an explicitly authorized `recover_plan` operation.
It scopes recovery to an owned plan, discovers only expired worker leases linked
to that plan, and delegates interruption/quarantine to the durable TaskRuntime.

Recovery is not retry: expired running tasks become interrupted, their plan
subtasks become failed, and a metadata-only plan event records the task and
specialist identity with `automatic_replay: false`. No handler or external
side effect is re-executed. A retry requires a new, explicitly submitted plan
after an operator has assessed any in-flight side effects.

The operation is bounded by the coordinator's 20-subtask plan limit and the
workflow store's 100-run expiry scan. It requires both `agent:recover` and
the TaskRuntime's per-task `task:recover` authorization. It does not recover
tasks belonging to other plans or owners.
