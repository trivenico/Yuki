# Agent Tool Event-Journal Failure After Side Effects Audit

## Summary

**Confirmed orchestration failure path:** `AgentExecutionLoop.run()` dispatches a tool batch and formats its results, then invokes the caller-provided `on_event` callback for `agent.tools_dispatched`. If that callback raises, the exception escapes the loop after the tool handlers have already run but before their results are appended to the model transcript.

In the conversation service, this exception is caught by the general agent-execution exception handler and converted into a generic failed assistant response. The generation can therefore be recorded as failed even though one or more tool operations completed successfully.

This is distinct from a tool handler returning `outcome_unknown`: the trigger here is a failure in the event/journal callback after the dispatch operation has returned.

## Source evidence

- `core/agent_execution_loop.py`
  - `_event()` calls `self.on_event(kind, details)` directly and documents event persistence as part of the execution contract.
  - In `run()`, the tool protocol dispatch and result formatting complete first.
  - The loop then increments the tool-call count and calls `_event("agent.tools_dispatched", ...)`.
  - Only after that callback returns does it append the formatted tool results to the in-memory transcript for the next model turn.
  - There is no compensation or durable checkpoint around the gap between successful dispatch and successful event recording.
- `core/conversation_service.py`
  - `agent_loop.run()` is inside a broad `except Exception` handler.
  - Any callback exception is mapped to status `failed` and a generic assistant response.
  - The service does not distinguish “tool execution succeeded but event journaling failed” from a model/provider or guarded-operation failure.

## Failure sequence

1. The model proposes a structurally valid tool batch.
2. `StructuredToolProtocol.dispatch()` invokes the guarded handlers and returns results.
3. The loop formats the results.
4. The loop calls `on_event("agent.tools_dispatched", ...)`.
5. The event callback raises, for example because a durable event journal is temporarily unavailable.
6. The exception escapes before the formatted tool results are appended to the transcript or another model turn can consume them.
7. `ConversationService` catches the exception and persists a generic failed response.
8. A user or caller may retry the task without knowing that the prior tool operation already completed.

The exact side effect depends on the registered handler. The source path establishes ordering and exception propagation; it does not establish that every tool is consequential or that a production retry occurred.

## Impact

- Successful tool side effects can be followed by a failed generation status and a generic failure message.
- The model does not receive the successful tool outputs, so it cannot incorporate them into a final response.
- A later user retry may repeat a non-idempotent action unless the target system or handler provides idempotency/reconciliation.
- The event journal can be missing the event that records successful dispatch, reducing auditability precisely when recovery is needed.

This finding is not an authorization bypass and does not imply the tool handler itself failed. It is a post-side-effect observability/orchestration failure.

## Recommended remediation

1. **Separate execution state from event delivery.** Persist a dispatch intent/receipt with stable call IDs before execution, and persist each completed result or outcome as close to the handler boundary as possible.
2. **Do not turn a post-dispatch journal failure into an ordinary retryable failure.** Surface a typed state such as `completed_needs_reconciliation` or `tool_results_unrecorded`.
3. **Make event delivery recoverable.** Use a durable outbox or append-only journal with retry semantics that do not require re-running the tool handler.
4. **Preserve returned results when telemetry fails.** If the event is observational rather than authoritative, isolate telemetry delivery from control flow. If it is authoritative, record enough durable dispatch state to resume transcript assembly without redispatch.
5. **Define idempotency requirements for consequential handlers.** Bind stable idempotency keys to generation and tool-call IDs where supported by target systems.
6. **Add deterministic fault-injection tests:**
   - A fake handler increments a side-effect counter and returns success.
   - The `agent.tools_dispatched` callback raises after dispatch.
   - Assert the handler ran exactly once, the model is not called for another turn, and the generation is represented as needing reconciliation rather than an ordinary retryable failure.
   - Add a recovery test proving event/result persistence can resume without invoking the handler again.
   - Add a separate test for event callback failure before any tool dispatch to ensure ordinary failure semantics remain accurate.

## Related existing audits

- `docs/STRUCTURED_TOOL_BATCH_FAILURE_PROPAGATION_AUDIT.md` covers losing machine-readable tool invocation outcomes and continuing after an `outcome_unknown` result.
- `docs/CONVERSATION_GENERATION_LEASE_LOSS_TOOL_SIDE_EFFECT_RACE_AUDIT.md` and `docs/CONVERSATION_GENERATION_CROSS_PROCESS_RECOVERY_INFLIGHT_TOOL_AUDIT.md` cover stale workers and in-flight side effects after lease loss.

Those findings are related but do not describe this specific ordering: successful dispatch followed by failure of the `agent.tools_dispatched` event callback before tool results enter the transcript.

## Severity and confidence

- **Severity:** Moderate (P2), conditional workflow correctness and auditability risk.
- **Confidence:** High for the call ordering and exception propagation visible in source.
- **Production occurrence:** Not established; no event callback implementation or production telemetry was exercised in this review.

## Review limitations

Source inspection at PR #1346 head `ceeb18972a73eb1a87f1336df7704418562041be`. No runtime code was changed, no tests were executed, and no production systems were inspected.
