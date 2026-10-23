# Phase 87: selected-provider specialist integration

The specialist worker can now build a handler registry from Yuki's selected provider adapter and an explicit mapping of coordinator agent IDs to supported roles (`research`, `coding`, `testing`, `reporting`). Provider selection remains owned by `ProviderManager`; this integration does not change credentials, silently switch providers, or bypass availability checks.

Handlers remain text-only and side-effect-free. The worker still uses durable task claims, approval enforcement, authorization, leases, version checks, and audit events. Register only agent IDs that exist in the coordinator configuration. No scheduler is started automatically, and this integration does not give specialists filesystem, browser, shell, or Git write permissions.

Validation includes tests for selected-provider mapping, missing provider factories, no selected provider, unsupported roles, bounded prompts/results, and existing worker/task safety behavior. Merge only after the exact-head cross-platform CI suite passes.