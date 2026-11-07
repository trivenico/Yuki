# Phase 285 — Explicit reviewer authorization injection

## Changes

The local worker-runtime factory now accepts an optional `verification_reviewer_authorizer` callback and passes it directly into `VerificationEvidenceStore`.

The callback is intentionally optional at construction so ordinary local development and artifact capture remain usable. However, report submission remains fail-closed: if the callback is absent, `VerificationEvidenceStore.record` rejects the report with `trusted_reviewer_authorizer_not_configured`.

This creates a clear dependency-injection seam for a deployment's authenticated identity provider and coordinator-backed policy adapter without inventing local agent credentials.

## Security boundary

- No reviewer identity is inferred from a report field.
- No permissive callback is installed by default.
- The local operator actor is not automatically treated as an independent reviewer.
- The coordinator adapter from Phase 284 still requires a trusted agent-to-principal resolver.
- The current built-in specialist registry does not yet provide distinct authenticated reviewer principals or dedicated reviewer execution handlers; independent review orchestration is therefore not enabled.

## Tests

Added a local runtime test confirming that the default evidence store has no reviewer authorizer and fails closed, while an explicitly injected callback is passed through unchanged.

Tests were authored but not executed. No production reviewer identity provider or reviewer-agent workflow is claimed.
