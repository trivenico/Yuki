# Phase 220 — agent execution observability

## Delivered

- Durable execution trace records with parent, root, agent, task and optional provider execution identifiers.
- Append-only lifecycle, delegation, provider, tool and permission event journal.
- Structured authorization decisions.
- Bounded input/output token, duration and cost counters.
- Root execution resource aggregation across delegated children.
- Bounded child/event queries and strict event/metric allowlists.

## Privacy and trust boundaries

No prompt, response text, credentials, arbitrary tool payload, or unbounded metadata is accepted by this API. Resource counters are caller-reported telemetry, not independently verified billing. This phase adds the trace store as an integration primitive; existing coordinator/provider execution paths are not yet universally instrumented.

## Deployment

SQLite uses local filesystem locking. For multi-host production deployment, migrate this journal to a transactional network database or dedicated telemetry service with equivalent uniqueness and append-only guarantees.
