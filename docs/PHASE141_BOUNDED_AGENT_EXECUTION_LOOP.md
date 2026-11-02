# Phase 141 — Bounded agent execution loop

## Delivered

`core/agent_execution_loop.py` composes a model callable with the canonical structured tool protocol. The loop supports text-only completion and repeated model/tool/model turns.

## Safety and resource boundaries

- Configurable model-turn, total tool-call, elapsed-time, and transcript-size limits.
- Cooperative cancellation before model turns and before tool dispatch.
- Tool proposals are parsed by the canonical structured protocol.
- Tool execution remains exclusively behind `StructuredToolProtocol` and `GuardedToolRuntime`.
- Tool results are returned as explicitly untrusted evidence.
- Model exceptions are wrapped without copying provider exception text into task events.
- Optional event callback can connect the loop to an external durable journal; journal failures fail closed.

## Explicit limitations

This first synchronous loop does not itself provide durable checkpoint storage, asynchronous cancellation of an in-flight provider request, streaming, token accounting, approval UI, or automatic specialist delegation. Provider adapters must enforce network timeouts. Durable event persistence is supplied by the caller through the event callback and should be integrated with the task runtime in a later phase.

## Validation

Regression tests cover successful tool round trips, text-only responses, turn/tool/transcript budgets, cancellation, malformed envelopes, model errors, event-journal failure, and invalid configuration. CI on the exact pull request head remains the merge gate.
