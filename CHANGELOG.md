# Changelog

## Unreleased

- **Phase 1 — CLI robustness:** handle empty `/skill` commands without indexing errors, isolate unexpected command-handler failures so the REPL can continue, and report configuration startup errors without exposing raw exception details.
- **Phase 1 CI blockers:** instantiate `DeviceScannerSkill` using its zero-argument constructor and reject unsupported provider names before constructing a provider adapter.
