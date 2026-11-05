# Phase 195 — explicit provider timeout handling

The durable operation store now has a dedicated timeout transition. It accepts
a bounded timeout duration (1–3600 seconds) and changes an in-flight operation
to `needs_reconciliation`, recording only the timeout classification and
retry prohibition. It does not store raw exception text or provider response
bodies.

The TaskRuntime wrapper requires the existing operation-resolution permission.
A timeout is deliberately not represented as provider failure or success:
the provider may have completed the side effect before the client timed out.
The operation cannot be dispatched again from this state; an authorized
reconciler must establish the outcome with provider-matched evidence.

This phase adds state-transition tests. It does not yet implement a concrete
provider HTTP client, enforce socket-level deadlines, or cancel remote work;
those belong in provider-specific execution adapters.
