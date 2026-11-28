# Structured Tool Batch Failure Propagation Audit

## Finding

**Confirmed tool-protocol failure propagation gap:** the structured batch dispatcher discards each guarded invocation's machine-readable status and continues to execute later calls even when an earlier handler returns `outcome_unknown`.

This can allow later consequential operations to run while an earlier operation's external side effect is unresolved. It can also allow the agent loop to continue and eventually report a completed response despite a tool in the batch having an unknown outcome.

## Source evidence

Reviewed at PR head `1d5251d9e8adc182dd83e2415face26c04370832`:

- `core/structured_tool_protocol.py`
- `core/guarded_tool_runtime.py`
- `core/agent_execution_loop.py`
- `tests/test_structured_tool_protocol.py`
- `tests/test_guarded_tool_runtime.py`
- `tests/test_agent_execution_loop.py`

### Status is available at the guarded runtime boundary

`GuardedToolRuntime.invoke_detailed()` returns a `ToolInvocationResult` with `ok`, `status`, and `value` fields.

Relevant outcomes include:

- `completed` after a handler returns.
- `denied` when policy or live-identity validation blocks execution.
- `outcome_unknown` when the handler was entered but raised an exception. The runtime explicitly documents that this cannot prove the target had no side effect and instructs callers to reconcile before retrying.

The compatibility method `invoke()` calls `invoke_detailed()` and returns only `.value`, discarding `ok` and `status`.

### Structured batch dispatcher loses the status and continues

`StructuredToolProtocol.dispatch()` parses the complete batch before dispatch, then iterates sequentially:

1. Serializes each call's arguments.
2. Calls `self._runtime.invoke(call.name, argument)`.
3. Wraps the returned string as a `StructuredToolResult`.
4. Proceeds to the next call unconditionally.

Consequently, an `outcome_unknown` result is reduced to ordinary output text. The dispatcher does not stop, mark the batch partial/uncertain, or expose a machine-readable failure status to the caller.

### Agent loop can proceed to a normal completion

`AgentExecutionLoop.run()` treats a successfully returned dispatch tuple as a dispatched batch, increments `calls_total`, formats the textual results as untrusted evidence, and sends them to the next model turn.

It does not receive per-call invocation statuses. If the model then returns a normal final answer, the loop emits `agent.completed` and returns status `completed`. The conversation service can persist that as a completed generation.

## Concrete failure sequence

1. A model proposes a batch containing tool A followed by tool B.
2. The protocol validates the entire JSON batch successfully.
3. Tool A's handler is entered and performs an external side effect, but then raises (for example, after the target accepted the request but before the handler received/processed its acknowledgement).
4. The guarded runtime returns `outcome_unknown`.
5. The protocol calls the compatibility `invoke()`, discarding the status and retaining only the explanatory string.
6. The protocol invokes tool B anyway.
7. The agent receives both textual outputs and can continue to a final answer.
8. The generation may be persisted as completed even though tool A's outcome is unresolved.

This is not a malformed-input partial execution: it occurs after the batch has passed complete structural validation and during normal sequential dispatch.

## Existing test coverage and gap

The inspected tests cover:

- Successful multiple calls preserving order and IDs.
- A malformed later call preventing any dispatch.
- A single policy-denied call not executing its handler.
- A single handler exception being represented by `outcome_unknown` through `invoke_detailed()`.

They do not cover a batch where the first handler returns `outcome_unknown` and verify that a later handler is not invoked. They also do not verify that the agent loop refuses to report ordinary completion after a batch with an uncertain tool outcome.

No tests were executed during this audit.

## Impact and boundaries

- **Impact:** a later tool in the same model-proposed batch can execute despite an earlier tool having an unresolved side-effect outcome.
- The agent loop can lose the distinction between a fully successful batch and one containing an unknown outcome.
- This can cause dependent or otherwise unsafe follow-on actions and misleading completion reporting.
- Each invocation still passes through its own guarded policy and live-identity checks. This finding is not a demonstrated authorization bypass.
- The exact external impact depends on registered handlers and their target systems. No production incident is established.

Policy denial is also represented as text and does not stop subsequent calls. Whether independent calls should continue after a denial is a product policy decision; the confirmed high-risk case is continuing after `outcome_unknown`.

## Recommended remediation

1. Have the protocol call `invoke_detailed()` and preserve structured status for every call. Avoid routing agent execution through the text-only compatibility wrapper.
2. Define explicit batch semantics:
   - Stop dispatch immediately on `outcome_unknown`.
   - Mark the batch as partially executed with an uncertain outcome.
   - Do not send an ordinary success-shaped tool result that hides the uncertainty.
   - Require reconciliation before dependent or retrying actions.
3. Consider stopping on any non-success status by default, with narrowly defined opt-in behavior for independent calls after a policy denial. Do not silently continue after an unknown outcome.
4. Propagate a typed batch result to `AgentExecutionLoop` so it cannot emit ordinary `agent.completed` status for a batch with unresolved tool outcomes. Persist an appropriate failed/needs-reconciliation generation state.
5. Preserve call IDs, statuses, and completed-prefix information for audit and operator reconciliation without storing sensitive arguments or outputs unnecessarily.
6. Add deterministic tests:
   - First handler mutates a fake external target and then raises.
   - Second handler increments a separate counter.
   - Assert the second handler is not entered.
   - Assert the returned batch state is explicitly uncertain/partial.
   - Assert the agent loop and conversation persistence do not report ordinary completed success.
   - Add a separate test to define the intended behavior for policy denial followed by an independent call.

## Review limitations

This is a source-level finding based on the cited files at the stated PR head. No runtime code was changed, no tests were executed, and no production deployment or incident data was inspected.
