# Phase 243 — Worker-side durable budget enforcement

**Status:** Agent execution loop now optionally enforces durable reservations. Runtime construction/call-site wiring, duration reservation, child allocation, and CI remain outstanding.

## Enforcement

When both `budget_store` and `budget_id` are supplied to `AgentExecutionLoop`:

- Reserve one model turn before each provider invocation (including streaming provider invocation).
- Reserve the prompt transcript character count before provider invocation.
- Reserve the complete parsed tool-call batch before guarded tool dispatch.
- Stop with `limit_reached` if the durable store reports resource exhaustion.
- Use a fresh execution ID per run and deterministic per-action idempotency keys within that run. A restarted run cannot reuse the prior run's model/tool allowance.

The existing guarded tool protocol remains the authorization boundary. The durable store is optional for compatibility; approved production task construction must require it rather than relying on the optional default.

## Accounting semantics and limitations

Reservations are conservative attempted-work accounting: a provider timeout still consumes its reserved model turn, and a failed tool dispatch still consumes its reserved batch. Transcript accounting charges each prompt sent, including repeated history. Duration continues to be bounded by the loop's monotonic in-process timer but is not yet charged durably. A parent ID alone does not allocate aggregate child budgets. The shared SQLite database must be used by all workers that need a common cap.

No claim is made that every Yuki worker uses this option until the task service and all loop construction sites are wired and audited.
