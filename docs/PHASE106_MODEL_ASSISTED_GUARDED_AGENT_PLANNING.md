# Phase 106 — model-assisted guarded agent planning

## Delivered

- Added `core/model_agent_planner.py` to request a bounded specialist-agent decomposition from the configured provider.
- The planner exposes only enabled specialist IDs, capabilities and permission labels as reference data.
- Model output must be a strict JSON object with an exact schema, bounded to 20 subtasks and 24,000 characters.
- Duplicate JSON object keys, markdown wrappers, extra fields, invalid types and malformed arrays are rejected.
- Every model-proposed subtask is required to request explicit human approval; a model response that sets `requires_approval=false` is rejected before plan submission.
- Parsed proposals are converted into `SubtaskSpec` values and passed to the existing `SpecialistAgentCoordinator`.
- The coordinator remains authoritative for capability matching, permission requirements, graph validation, authorization, idempotency, dependency gating, concurrency limits and durable task creation.
- The planner advances the plan once; it does not execute tools, run specialist workers, or bypass approvals.
- Approval-required tasks remain in the durable `queued` state, but `WorkflowRunStore.claim_next()` refuses to lease them until an authenticated approval is persisted for that exact task definition.
- Added tests for valid routing, dependency-gated dispatch, malformed model output, rejected approval suppression, worker claim denial before approval, unknown capabilities, invalid goals and unauthorized actors.
- Registered the capability as experimental in the evidence-backed manifest.

## Security boundaries

Model-generated plans are untrusted proposals, never permissions. Actor identity must come from a trusted caller. The coordinator and TaskRuntime perform independent authorization. The planner rejects any proposal that disables approval, and the workflow store blocks worker claims until explicit approval is recorded against the exact task definition. Specialist worker execution remains a separate subsystem.

## Current limitations

- Provider call is synchronous; no streaming planning UX.
- One planning call per invocation; no repair/replan loop.
- The coordinator's workers remain separate; this phase does not wire the specialist worker executor into the conversation UI.
- No hosted authentication, multi-tenant identity adapter, or desktop approval UI.
- The planner does not grant or infer missing permissions; unsupported capability requirements fail closed.

## Validation

```bash
python -m unittest tests.test_model_agent_planner -v
python -m unittest tests.test_agent_coordinator -v
python -m unittest tests.test_capability_manifest -v
python -m unittest discover -s tests -v
```

Require exact-head Linux and Windows CI before merge.
