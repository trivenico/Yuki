# Phase 18 — Complete Runtime Configuration Type Boundaries

## Audit findings
Phase 17 bounded common string settings, but review found remaining coercions in enumerated settings (`language_behavior`, `voice_style`, and `spoken_reply_policy`), a display name that was validated but then discarded, and list entries that had count limits without per-entry length limits. The duplicate-key regression fixture also needed correction to ensure it actually exercised a duplicate YAML mapping.

## Implemented
- Removed implicit string coercion for enumerated settings and validated them with bounded-string rules.
- Preserved the configured display name after validation rather than hardcoding the returned value.
- Added per-item length limits for trusted plugin module identifiers, target allowlists, sandbox image names, and web host allowlists.
- Corrected the duplicate-key and size test fixtures to use real line breaks.
- Added tests for non-string enum values, empty display names, and overlong host allowlist entries.

## Verification
Earlier CI passed for the previous commit, but this phase added new code and tests. A new successful workflow run for the exact latest head is required before treating the changes as verified.

## Remaining release gates
1. Create one versioned canonical configuration schema shared by onboarding and runtime.
2. Add reversible YAML-to-JSON migration with backup, rollback, and compatibility tests.
3. Add full config contract tests for every key, environment override, and nested structure.
4. Run full CI, type/lint checks, dependency/secret scans, and integration tests against the exact head.
5. Keep the pull request in draft pending human security review and runtime/deployment validation.

## Readiness estimate
Remain at 40% (engineering estimate, medium-low confidence). Stronger parsing is a foundational improvement, not proof of end-to-end production readiness.