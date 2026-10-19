# Changelog

## Unreleased

- **Phase 1 — CLI robustness:** handle empty `/skill` commands without indexing errors, isolate unexpected command-handler failures so the REPL can continue, and report configuration startup errors without exposing raw exception details.
