# Phase 80 — authenticated workflow approval

## Invariant

A protected workflow can only receive durable approval through an application boundary that resolves the request into a trusted principal, checks the workflow approval permission, and optionally applies resource-level authorization. The store persists the approver and a SHA-256 hash of the authenticated session identifier alongside the exact request digest.

## Integration contract

- Inject a `principal_resolver(request_context)` that reads identity from the application's authenticated server-side session or verified identity middleware. Do not build the principal from request-body actor/session fields.
- Return an `AuthenticatedPrincipal` with a non-empty actor ID, session ID, and immutable permission set.
- Require the `workflow:approve` permission.
- Provide the optional resource authorizer when approvals depend on project membership, ownership, risk level, or environment.
- Any resolver or authorizer error fails closed.
- Keep direct `WorkflowRunStore.approve` access inside trusted server-side code only.

## Persistence and migration

The workflow store adds an additive `approved_session_hash` column. Existing approval records created before this field existed are not treated as valid for protected claims after migration. Session identifiers are hashed before persistence; raw session IDs are not stored.

## Scope limits

This phase adds a reusable authenticated approval boundary; it does not claim a production HTTP route or identity provider is wired into Yuki's deployed application. The web/API integration must inject the actual authentication middleware. Session revocation, approval expiry, MFA/step-up authentication, and multi-approver policy remain follow-up controls for high-risk operations.

Run workflow-store and approval-service tests plus all required cross-platform quality gates on the exact PR head before merge.
