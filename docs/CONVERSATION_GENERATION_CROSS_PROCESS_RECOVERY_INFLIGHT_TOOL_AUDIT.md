# Cross-Process Generation Recovery and In-Flight Tool Execution Audit

## Scope

Reviewed `core/conversation_service.py`, `core/conversation_store.py`, `core/agent_execution_loop.py`, and `tests/test_conversation_service.py` on PR #1346 branch `research/reference-projects-integration-assessment`.

Source inspection only. No runtime code or tests changed or executed.

## Confirmed finding: lease expiry can permit recovery and a new generation while the old worker is still inside synchronous tool dispatch

### Relevant control flow

1. Every `ConversationService.reply()` acquires a database lease before entering `_reply_unleased()`. The lease is shared across service instances and keyed by conversation, with a unique token and finite expiry.
2. A heartbeat renews the lease periodically. If renewal returns no matching live lease or raises, the heartbeat sets its `lost` event.
3. `AgentExecutionLoop` checks cancellation before each model turn and immediately before tool dispatch. The tool dispatch itself is synchronous and has no cancellation callback or lease-token check while it runs.
4. If a tool dispatch has already started when the lease expires or heartbeat fails, that call can continue until the tool implementation returns or raises.
5. After expiry, another service instance can acquire a replacement lease. Its `_reply_unleased()` lists all durable `running` generations and reconciles the old run. If no assistant response exists, it appends an interruption assistant message and marks the run `interrupted`.
6. The replacement worker can then proceed with its own request while the old worker's synchronous tool dispatch may still be running.
7. When the old dispatch returns, the old loop checks cancellation at the next model-turn boundary. At the service layer, the old worker also checks `lease_lost` after `agent_loop.run()`; it cannot persist its final assistant response without the current live matching lease token.

### Why existing fencing is not sufficient

The lease token fences database persistence, not external side effects already underway. The synchronous `tool_protocol.dispatch()` operation is not forcibly interrupted when heartbeat renewal fails. Recovery can therefore classify the durable generation as interrupted and allow a replacement request while the prior tool operation has not yet reached a safe stopping point.

The local `ConversationService._lock` does not coordinate separate processes. Cross-process serialization depends on the database lease, and lease expiry intentionally permits a replacement worker to proceed.

### Impact

This creates a concurrency/side-effect ambiguity window after lease expiry or heartbeat loss:

- A previously authorized tool call may still complete after its generation has been marked interrupted.
- A new request can start and may issue overlapping or semantically duplicate actions.
- The old worker's final assistant result is fenced from persistence, so the user may not see a reliable record of the old tool's eventual outcome.
- Whether an external side effect is duplicated or conflicts depends on the concrete tool and whether it has its own idempotency key, cancellation, or fencing support.

This is not evidence that the lease can be bypassed while still live, nor that a stale worker can write the final assistant message. It is a limitation of lease-based coordination when external synchronous work outlives the lease.

## Recommended remediation

1. **Bound tool execution below the lease safety margin.** Require tool adapters to enforce finite operation deadlines and propagate cancellation where supported.
2. **Use operation-level idempotency.** Pass a stable generation/tool-call idempotency key to side-effecting tools and require downstream deduplication where available.
3. **Fence at the side-effect boundary.** For tools under Yuki's control, validate a current lease/fencing epoch immediately before committing an irreversible action. A check only before entering a long-running operation is insufficient.
4. **Separate recovery detection from recovery authorization.** Consider a grace/reconciliation state for expired runs whose worker may still be draining, rather than immediately allowing a new side-effecting generation.
5. **Persist tool-call lifecycle.** Record dispatched/started/completed/unknown outcomes with stable call IDs so recovery can distinguish “not dispatched” from “possibly completed.”
6. **Add deterministic cross-instance tests.** Block a tool dispatch after it starts, expire/lose the lease, let a second service instance acquire and recover, and assert the defined behavior. Include a side-effect counter to prove whether the old call can finish after recovery.
7. Test a tool that supports cooperative cancellation separately from one that cannot be interrupted; document the residual ambiguity for the latter.

## Existing protections verified

- A live lease prevents a second worker from acquiring the conversation slot.
- Renewal requires the same owner/token and an unexpired lease; an expired worker cannot revive it.
- Assistant persistence requires a live matching lease token.
- The old worker cannot release a replacement worker's lease because release is token-matched.
- The agent loop checks cancellation before tool dispatch and before the next model turn.
- Recovery does not automatically replay the previous model/tool sequence.

## Severity and confidence

- **Classification:** concurrency correctness / external side-effect lifecycle.
- **Confidence:** high for the control-flow window; actual duplicate or conflicting side effects are tool-dependent.
- **Trigger:** lease expiry or heartbeat renewal failure while a synchronous tool dispatch is still running.
- **Production occurrence:** not established.

## Validation status

- Source inspection only.
- No tests executed.
- No runtime code changed.
