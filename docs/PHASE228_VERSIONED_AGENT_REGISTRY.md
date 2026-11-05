# Phase 228 — versioned specialist registry manifests

- Added strict numeric MAJOR.MINOR.PATCH version, risk tier, timeout budget and concurrency ceiling to specialist manifests.
- Persisted the new fields in SQLite with backward-compatible schema migrations and safe defaults for existing registries.
- Included execution-relevant fields in the agent identity digest so planned tasks detect manifest changes before dispatch.
- Sanitized registry listing exposes operational metadata while continuing to withhold permission grants.
- Registration remains separately authorized and changes metadata only; it does not install code, grant permissions, or activate a worker.

Limits: input/output JSON Schemas, model/provider requirements, live readiness probes, concurrency enforcement in the worker pool, and retirement workflows are not implemented here. `max_concurrency` is declared policy, not yet an enforced semaphore. Risk tier does not replace permission checks or human approval.
