# Phase 198 — behavioral provider contract validation

The provider adapter now validates normalized chat responses against Yuki's
structured tool protocol, rejects tool names that were not included in the
request, validates finish-reason types and rejects unknown response fields.

Streaming is now consumed through a validating iterator. It accepts only
non-empty text deltas and a final normalized response event, checks deadline
and cancellation between events, and closes the underlying iterator when
consumption ends or validation fails.

These checks prevent malformed or unrequested tool calls from crossing the
adapter boundary. They do not execute tools, prove provider honesty, interrupt
a blocked network read, or cancel remote provider work. Cancellation is
cooperative and checked when control returns to the iterator. Provider network
timeouts and durable uncertain-outcome reconciliation remain separate controls.
