# Agent Tool Batch Dispatch After Conversation Lease Loss

## Summary

The conversation service detects generation-lease loss and fences assistant-message persistence, but a stale agent worker can continue dispatching later calls in an already-started tool batch. The structured tool protocol has no cancellation or generation-lease check between individual tool invocations, and the guarded runtime validates owner/session identity and tool policy rather than the conversation-generation lease.

This is a confirmed execution-lifecycle gap. It can permit side effects to continue after the worker has lost the conversation generation lease and another worker may have acquired it. The precise impact depends on the registered tools and their side effects; this review does not establish that every tool is affected or that an in-flight handler can be interrupted.

## Affected code

- `core/conversation_service.py`
  - `_GenerationLeaseHeartbeat._run()` sets `lost` when lease renewal fails or no longer succeeds.
  - `reply()` supplies a cancellation callback that observes `heartbeat.lost`.
  - `_reply_unleased()` checks lease loss after `agent_loop.run()` and fences assistant persistence by passing `lease_token` to `append_message()`.
- `core/agent_execution_loop.py`
  - Checks cancellation between model turns and immediately before tool batch dispatch.
  - Calls `tool_protocol.dispatch(encoded_envelope)` as one synchronous operation, with no cancellation check between calls.
- `core/structured_tool_protocol.py`
  - `dispatch()` validates the complete envelope and then iterates through every call, invoking the runtime without a cancellation or lease-fence callback.
- `core/guarded_tool_runtime.py`
  - Revalidates registered implementation identity, owner/session liveness, and policy authorization before each individual handler invocation.
  - It does not receive or validate the conversation generation lease token.

## Execution scenario

1. Worker A owns a conversation generation lease and receives a model response containing multiple tool calls.
2. Worker A enters `StructuredToolProtocol.dispatch()`; the first handler blocks or performs a long-running operation.
3. The lease renewal thread reports a failure or loss of ownership and sets the heartbeat's `lost` event.
4. Worker A's active tool handler returns.
5. The protocol's dispatch loop proceeds to the next call without consulting the heartbeat or revalidating the conversation lease.
6. After the lease expires or is otherwise no longer held, worker B can acquire the conversation lease and begin recovery.
7. Worker A may still execute remaining calls even though it can no longer persist its assistant response.

The existing cancellation check before batch dispatch does not close this race: lease loss can occur after that check and during any earlier call in the batch. The final assistant-message persistence fence prevents a stale response from being appended, but it is not a side-effect fence.

## Impact and boundaries

- Later calls in a multi-call batch may execute after generation ownership has been lost.
- A new worker may concurrently recover or continue the conversation while the stale worker is still performing side effects.
- A handler already entered when lease loss occurs may complete; cancellation cannot retroactively prove that it had no effect.
- The existing guarded runtime's owner/session and policy checks remain useful, but they do not establish that the invoking worker still owns the conversation generation.
- This finding does not demonstrate that a single in-flight tool can be forcibly interrupted, nor that all registered tools have externally visible side effects.
- Recovery intentionally avoids automatically replaying interrupted model/tool sequences, which reduces duplicate execution on recovery but does not stop the stale worker's in-progress batch.

## Recommendations

1. Pass a cancellation/fencing callback into structured dispatch and check it immediately before every individual invocation. Stop dispatching subsequent calls once cancellation or lease loss is observed.
2. Enforce the generation fence at the guarded side-effect boundary, not only in conversation persistence. Bind the invocation to a durable conversation/generation identity and validate the current lease token immediately before handler entry.
3. Define behavior for lease loss while a handler is already running. Mark its outcome as unknown, do not automatically retry it, and provide reconciliation for side-effecting integrations.
4. Where a tool supports idempotency, derive a stable invocation key from the generation/request ID and tool-call ID, and pass it to the downstream system. Do not treat local in-memory deduplication as sufficient for external side effects.
5. Add deterministic concurrency tests: block the first handler, revoke/expire the lease, release the handler, and assert that later calls are not entered; separately test lease loss during a single handler and verify the outcome is recorded as unknown.

## Severity and confidence

- **Severity:** Medium, conditional on multi-call batches and side-effecting tools.
- **Confidence:** High for the missing cancellation/fencing check in the inspected dispatch path; production exploitability depends on deployment lease behavior, tool mix, and whether the guarded policy/identity validator independently prevents the stale invocation.
- **Scope:** Source review of the conversation service, generation store, agent loop, structured tool protocol, and guarded runtime. No runtime tests or production composition tests were executed.

## Suggested remediation boundary

The strongest fix is a generation-aware invocation context enforced by the guarded runtime for each call, combined with a cancellation check between calls in the batch. A callback-only check improves behavior but does not provide a durable side-effect fence if lease ownership changes between the check and handler entry. For external systems, downstream idempotency and reconciliation remain necessary for uncertain in-flight outcomes.
