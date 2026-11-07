# Phase 197 — provider adapter conformance inspection

Added `providers/adapter_conformance.py` to inspect the actual configured
provider object's callable signatures before routing or enabling optional
features.

The report distinguishes chat, streaming interface, cooperative execution
context and streaming execution context. It emits only class names, booleans,
and fixed evidence labels; it does not expose credentials, endpoints, or
provider responses.

This is **structural inspection, not behavioral certification**. A method
accepting `execution_context` may ignore it; a streaming method may yield
invalid events. Cancellation/deadline behavior, tool-call envelopes, image
handling, and provider idempotency still require adapter-specific conformance
tests. No capability is inferred from model marketing metadata.
