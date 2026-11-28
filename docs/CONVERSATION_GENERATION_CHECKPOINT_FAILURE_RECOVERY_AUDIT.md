# Conversation Generation Checkpoint Failure Recovery Audit

## Scope

Reviewed `core/conversation_service.py`, `core/conversation_store.py`, and `tests/test_conversation_service.py` on PR #1346 branch `research/reference-projects-integration-assessment`.

Source inspection only. No runtime code or tests changed or executed.

## Confirmed finding: initial checkpoint failure is later misclassified as an interrupted model execution

### Evidence

1. `ConversationService._reply_unleased()` first persists the user message with `generation_request_id=request_id`. The store transaction creates both the user message and a generation row with status `running`.
2. Immediately afterwards, the service calls `update_generation_checkpoint(... checkpoint={"phase": "model_running"})`.
3. This checkpoint write occurs before the `try/except Exception` block that wraps `agent_loop.run()`.
4. If checkpoint persistence raises, the exception propagates. The model/provider is not invoked and no assistant response is written.
5. The outer `reply()` finally block stops the heartbeat and attempts to release the lease. It does not mark the generation failed or cancelled.
6. On a subsequent request, recovery enumerates all durable `running` generations. When it finds a user message without a following assistant message, it appends the standard “response was interrupted before completion” assistant message and marks the run `interrupted`.

### Impact

A transient database failure during the initial checkpoint write can leave a durable running record for work that never started. Recovery later tells the user that the response was interrupted, although the model/provider was never called.

The request is not automatically replayed, which is appropriately conservative for potentially side-effecting work. However, the terminal state and user-facing explanation are inaccurate, and the original request is not completed.

This is a correctness/recovery-class defect. No evidence indicates unauthorized execution, duplicate provider calls, or a permanently held lease.

### Recommended remediation

- Move initial checkpoint persistence into an explicit generation state transition with a recoverable terminal outcome.
- Distinguish at least `queued/accepted`, `starting`, `running`, and terminal states, or record a durable phase before the provider invocation.
- On checkpoint failure before provider invocation, attempt to mark the generation `failed` (or a dedicated `not_started` state) before propagating the storage error.
- Make recovery messages phase-aware: do not claim execution was interrupted unless the durable record proves the model/tool phase actually began.
- Add a fault-injection test where `update_generation_checkpoint` raises on its initial call; assert the model is not called and recovery records a truthful not-started/failed outcome rather than an interrupted response.
- Add a separate test for a failure after provider invocation begins to preserve the current no-automatic-replay safety policy.

## Related protections verified

- User message and generation record are inserted atomically by `append_message`.
- The model call is not reached until after the initial checkpoint write.
- The outer `finally` releases the generation lease token on ordinary exceptions.
- Recovery intentionally avoids automatically replaying possibly side-effecting model/tool work.
- If an assistant message was persisted before a crash, recovery can mark the run completed instead of appending an interruption marker.

## Severity and confidence

- **Classification:** correctness / durable workflow recovery.
- **Confidence:** high; the checkpoint write is visibly outside the provider exception handler, and recovery treats all remaining running records as interrupted.
- **Impact:** a request that never started can be represented as interrupted; the user may need to submit it again.
- **Production occurrence:** not established.

## Validation status

- Source inspection only.
- No tests executed.
- No runtime code changed.
