# Phase 202 — central provider gateway

- Added a gateway adapter used by the owner-scoped reasoning runtime before decorators and agent-loop construction.
- Rejects mock/test adapters.
- Requires trusted owner-scoped opt-in when an adapter explicitly declares privacy_class="cloud_provider".
- Preserves the normalized chat, streaming, deadline and cancellation adapter contract.
- No provider fallback, health probe, retry, or network request is performed by gateway construction.

Limitations:
- The gateway currently covers the owner-scoped reasoning runtime only; other worker, callback, scheduler and direct provider paths still need audit.
- Providers without a privacy_class are classified as unknown, not assumed local.
- cloud_opt_in_for_owner must be supplied by trusted application authorization, never conversation input.
