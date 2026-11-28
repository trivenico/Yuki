# Conversation Generation Cancellation Completion Audit

## Scope

Reviewed `core/conversation_service.py`, `core/agent_execution_loop.py`, `core/conversation_store.py`, and `tests/test_conversation_service.py` on PR #1346 branch `research/reference-projects-integration-assessment`.

This is a bounded source audit. No runtime code or tests were changed or executed.

## Confirmed finding: cancellation arriving during a non-streaming model call can be reported as successful completion

### Evidence

1. `ConversationService.reply()` passes a combined cancellation predicate to `AgentExecutionLoop.run()`. It reports cancellation when the caller's cancellation callback is true or the generation lease heartbeat has been lost.
2. `AgentExecutionLoop.run()` checks cancellation at the top of each model turn, before dispatching parsed tool calls, and between streamed events.
3. After the model adapter returns, the loop validates the response and parses tool calls. If there are no tool calls and the returned text is non-empty, it emits `agent.completed` and returns `AgentExecutionResult("completed", ...)` immediately.
4. That no-tool completion branch does not re-check the cancellation predicate after the model call returns.
5. `ConversationService._reply_unleased()` maps the returned status to a completed assistant message and persists it. It does not independently check the caller cancellation predicate after `agent_loop.run()` returns.
6. The existing cancellation test covers cancellation already true when generation begins; it does not exercise cancellation becoming true while a blocking, non-streaming model call is in progress.

### Impact

If a user cancels while a non-streaming provider request is already in flight, and the provider subsequently returns an ordinary final text response without tool calls, the service can persist and report that response as `completed` rather than `cancelled`.

This does not demonstrate that cancellation can interrupt an already-running synchronous provider request. The narrower, confirmed issue is that cancellation is not honored when deciding the terminal result after that request returns. A user may see a response treated as completed after asking to stop it.

For a response containing tool calls, the loop does check cancellation before dispatch, so this finding is specifically about the no-tool final-answer branch. The conversation service separately checks lease loss after the loop returns, so this finding does not establish stale-lease result persistence.

### Recommended remediation

- Re-check the cancellation predicate immediately after each model/stream operation returns and before accepting a final no-tool answer.
- Prefer a final cancellation check in `ConversationService._reply_unleased()` before persisting the assistant result as defense in depth.
- Define whether a cancellation arriving after the provider has completed but before persistence should win; implement and document one consistent linearization point.
- Add a deterministic test using a blocking model stub: start the reply, wait until the model call begins, set the cancellation event, release the model with a valid final text/no-tool response, and assert the terminal result and persisted assistant message are cancelled rather than completed.
- Add a companion streaming test for cancellation at stream exhaustion/final-response boundaries.

## Other generation-state observations

- A durable running generation is reconciled on the next request rather than replaying potentially side-effecting model/tool work.
- If an assistant response was already persisted before a crash, recovery marks the run completed.
- If the user message exists without an assistant response, recovery appends an explicit interrupted marker and marks the run interrupted.
- Generation lease renewal is performed by a heartbeat; loss is propagated through the cancellation callback and checked again before the service persists a result.
- Assistant persistence is fenced by the live lease token.
- The outer `finally` stops the heartbeat and releases the matching lease token.

These mechanisms reduce duplicate execution and stale-worker persistence risk; they do not remove the cancellation-completion gap above.

## Severity and confidence

- **Classification:** correctness / cancellation semantics (not a demonstrated authorization bypass).
- **Confidence:** high; the branch behavior is explicit in source.
- **Exploitability:** not applicable as a security exploit. Requires cancellation to occur during an in-flight non-streaming model request which then returns a normal final answer.
- **Runtime impact:** user cancellation may be ignored for the terminal status and persisted response.
- **Production occurrence:** not established.

## Validation status

- Source inspection only.
- No tests executed.
- No runtime code changed.
