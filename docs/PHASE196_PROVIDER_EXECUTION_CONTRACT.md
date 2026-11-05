# Phase 196 — provider execution contract

Added a provider-neutral execution context with a monotonic deadline,
remaining-time inspection, cooperative cancellation event and explicit
`ProviderCancelled` / `ProviderDeadlineExceeded` outcomes.

`ProviderAgentAdapter` validates bounded deadlines (0.1–3600 seconds) and
passes `execution_context` only to provider methods that explicitly declare
that parameter, preserving compatibility with existing adapters. It checks
cancellation/deadline before and after calls.

Important limitation: a legacy synchronous provider that does not accept and
honor the context cannot be forcibly interrupted. If it returns after the
deadline, Yuki reports an uncertain deadline outcome; the provider may have
completed work. Streaming setup is guarded, but stream iteration needs a
provider-aware iterator wrapper in a later phase. No provider is claimed to
support hard cancellation unless its implementation cooperates.
