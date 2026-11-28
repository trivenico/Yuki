# Conversation Generation Lease-Loss and Concurrent Tool Side-Effect Audit

## Finding

**Confirmed reliability and side-effect coordination race:** an expired generation lease fences conversation persistence, but does not fence a tool handler that was already entered by the previous worker. A replacement worker can recover the old generation as interrupted and begin another request while the old handler is still executing.

This is a conditional race requiring a heartbeat renewal failure (or equivalent lease loss), lease expiry, and a tool handler that remains in flight. No production occurrence is established.

## Source trace

Reviewed at PR head `366646df31e59294ee3683f5be664e245db664f0`:

- `core/conversation_service.py`
- `core/conversation_store.py`
- `core/agent_execution_loop.py`
- `core/structured_tool_protocol.py`
- `core/guarded_tool_runtime.py`

### Lease and heartbeat behavior

1. `ConversationService.reply()` acquires a database lease with a unique token and starts `_GenerationLeaseHeartbeat`.
2. The heartbeat calls `renew_generation_lease()` periodically. Any exception or a `None` renewal sets `heartbeat.lost` and exits the heartbeat thread.
3. A renewal exception does not synchronously stop the request thread or forcibly interrupt a synchronous provider/tool call.
4. The database lease remains until its recorded expiry unless explicitly released. After expiry, `acquire_generation_lease()` permits another worker to replace the lease with a new token.
5. The new worker enters `_reply_unleased()` and lists every durable generation whose status remains `running`. This recovery query does not distinguish a genuinely abandoned worker from an old worker whose lease was lost but whose operation is still executing.

### Tool execution behavior

1. `AgentExecutionLoop.run()` checks cancellation before dispatching a parsed tool batch.
2. It then calls `StructuredToolProtocol.dispatch()`, which invokes each call sequentially.
3. `GuardedToolRuntime.invoke_detailed()` checks the authenticated identity and policy, revalidates identity immediately before entering the handler, and then calls the synchronous handler.
4. The handler receives no generation lease token, lease-loss event, or target-side fencing value. There is no cooperative cancellation check inside the handler boundary.
5. If lease loss occurs after the handler has started, the handler can continue. When control eventually returns to `ConversationService`, the service checks the lease-lost event before accepting the agent result and assistant persistence is additionally fenced by the lease token.

The existing checks therefore prevent a stale worker from committing an assistant response after losing its lease, but they do not prevent a handler already in progress from completing its external side effect.

## Concrete interleaving

- Worker A owns token A and dispatches a consequential tool handler.
- A's heartbeat encounters a transient store/database error and marks token A as lost.
- The handler in A remains blocked or continues performing its external operation.
- Token A's lease expires.
- Worker B acquires token B for the same conversation.
- B observes A's generation still marked `running`, appends the interrupted marker, and marks that generation `interrupted`.
- B starts a new request. If that request repeats the same logical action, its handler can overlap with A's still-running handler.
- A's handler may then finish its side effect. A cannot persist its assistant response because its lease has been lost.

This can produce duplicate or overlapping external effects even though the conversation store has correctly fenced stale assistant persistence.

## Impact and boundaries

- **Impact:** conditional duplicate/overlapping side effects across workers after lease loss and expiry.
- **Not established:** a production incident, a general authorization bypass, or an ability for an unauthenticated caller to invoke tools.
- Existing identity and policy checks still apply when each handler is entered.
- The race is most relevant to slow or blocking tools with non-idempotent external effects.
- The database lease is a coordination mechanism for conversation state; it is not, by itself, a fencing mechanism understood by external targets.

## Recommended remediation

1. Treat lease loss as a durable execution-state transition, not only a local cancellation signal. Preserve a distinct `outcome_unknown` state when a handler may already have crossed its side-effect boundary.
2. Do not automatically reconcile a lease-expired generation as safely interrupted while an external handler may still be executing. Require a bounded grace/worker-liveness protocol or an explicit operator/target reconciliation policy.
3. Propagate an execution context containing cancellation and lease identity into the guarded tool boundary. Check it immediately before handler entry. This narrows but cannot eliminate the race once a synchronous handler has begun.
4. For consequential external actions, require target-side idempotency keys and/or monotonic fencing tokens that the target validates atomically with the side effect. A local pre-call check alone is insufficient.
5. Make tool handlers support bounded cancellation where possible, while retaining `outcome_unknown` when cancellation cannot prove that the target did not act.
6. Add deterministic multi-worker fault-injection tests:
   - Block a handler after it enters.
   - Force heartbeat renewal to fail.
   - Expire/reclaim the lease from a second service instance.
   - Trigger recovery and a second logical request.
   - Verify that the target-side idempotency/fencing contract prevents duplicate effects, or that the second request is blocked pending reconciliation.
   - Release the first handler and verify stale assistant persistence remains rejected.
7. Add a regression test showing that a generation with an in-flight/unknown tool outcome is not silently represented as safely interrupted.

## Review limitations

This was a source-level review of the cited files at the stated PR head. No runtime code was changed, no tests were executed, and no production deployment or incident data was inspected.
