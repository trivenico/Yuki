# Phase 229 — bounded agent input/output contracts

- Added dependency-free contract validation for agent input and output payloads.
- Supports a documented JSON Schema subset: object, array, string, integer, number, boolean and null; required/properties; additionalProperties=false; items; enum; bounded lengths/counts and numeric ranges.
- Rejects remote references, unknown keywords, malformed schemas, non-JSON values, NaN/infinity, oversized payloads and excessive nesting.
- Returns bounded path/code diagnostics without echoing sensitive input values.
- Includes explicit input and output validation helpers and tests for valid, invalid, type-confusion and resource-boundary cases.

Integration limitation: this module is a reusable validation boundary but is not yet wired into coordinator dispatch or worker result persistence. Agent manifests do not yet persist schemas or provider/model requirements. Runtime readiness is still a separate phase; this module makes no claim that a provider is reachable or live verified.
