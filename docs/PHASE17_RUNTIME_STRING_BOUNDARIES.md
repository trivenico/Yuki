# Phase 17 — Runtime Configuration String Boundaries

## Audit finding
After rejecting unknown keys and invalid scalar types, several runtime settings still relied on implicit string coercion or only had a narrow limit on the model field. This could turn malformed YAML values into surprising runtime values and allowed unnecessarily large path/endpoint values.

## Implemented
- Added a shared bounded-string validator that rejects non-string values instead of coercing them.
- Applied explicit maximum lengths to names, workspace paths, provider/model fields, voice settings, and endpoint/model paths.
- Validated environment-overridden workspace and Ollama endpoint/model values with the same type and length rules.
- Added regression tests for non-string STT/TTS/path values and oversized workspace paths.

## Verification
The changes and regression tests were committed to the feature branch. Current-head CI must be queried after the latest commit; a prior green workflow for an earlier commit does not verify this new head.

## Remaining release gates
1. Introduce one canonical versioned configuration schema and a reversible YAML-to-JSON migration with backup and rollback.
2. Apply strict nested validation to every configuration field and verify environment override precedence.
3. Run the full test suite and security scans against the exact current head.
4. Add end-to-end tests across CLI, TUI, providers, MCP, scheduler, daemon, and desktop startup.
5. Keep the PR in draft until CI, integration testing, and human security review pass.

## Readiness estimate
Remain at 40% (medium-low confidence). String-boundary hardening improves input safety but does not establish complete runtime integration or deployment readiness.