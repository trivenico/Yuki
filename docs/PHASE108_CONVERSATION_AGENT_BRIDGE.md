# Phase 108 — persistent conversation to agent bridge

## Delivered

- Added `core/conversation_agent_bridge.py` to connect workspace-scoped persistent conversation turns to the guarded `AgentExecutionService`.
- Added an explicit `submit_agent_turn()` entry point. Ordinary conversational turns remain tool-free; this bridge is used only when an application explicitly chooses agent-task submission.
- Persists the user request and acquires the existing single-active-turn lease before planning.
- Uses the request ID as the specialist plan idempotency key, preventing duplicate plans for a replayed request.
- Persists a concise assistant response describing the durable plan and its approval requirements.
- Replays completed requests from conversation storage without calling the model planner again; recovers the plan ID from the persisted response and obtains current status through the coordinator's authorization boundary.
- Rejects an in-progress duplicate without submitting another plan.
- Requires a trusted actor ID from authentication middleware and a workspace ID from the caller's trusted scope.
- Returns safe, bounded failure information and marks the conversation turn failed when planning fails.
- Added integration tests for durable plan creation, approval-gated worker blocking, completed request replay and unauthorized actors.
- Registered the bridge as an experimental, evidence-backed capability.

## Security boundaries

- This bridge is not an authentication system. Actor and workspace identity must be injected by trusted application middleware; never accept them as authoritative values from the user prompt or request body.
- The bridge does not invoke tools or workers. It delegates to the existing planner/coordinator and returns the plan.
- Model-generated proposals remain untrusted; the planner rejects attempts to disable human approval.
- Specialist execution remains separately controlled by authenticated approval and the durable workflow store.
- Conversation persistence and agent task persistence are separate databases. Request-ID idempotency prevents ordinary replay duplication, but this phase does not provide a distributed transaction across both databases. A process crash between plan creation and conversation completion requires reconciliation; the shared idempotency key makes a safe retry possible.

## Not included

- Automatic natural-language intent classification. The caller must explicitly select agent-task mode.
- Token streaming, SSE/WebSocket events, hosted API authentication, CLI and desktop UI wiring.
- Cross-database transactional outbox or background reconciliation worker.
- Continuous worker daemon; the existing finite worker-cycle interface remains.

## Validation

```bash
python -m unittest tests.test_conversation_agent_bridge -v
python -m unittest tests.test_conversation_runtime -v
python -m unittest tests.test_agent_execution_service -v
python -m unittest tests.test_capability_manifest -v
python -m unittest discover -s tests -v
```

Require exact-head Linux and Windows CI before merge. This phase is stacked on Phase 107.
