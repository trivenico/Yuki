# Phase 164 — Authenticated Income Opportunity API

## Objective

Expose the persistent Phase 163 opportunity pipeline to the authenticated Yuki web runtime without allowing request payloads to choose the owner identity.

## Routes

All routes require a valid browser owner session:
- `GET /v1/income/opportunities` — list opportunities; supports bounded `status`, `platform_id` and `limit` query parameters.
- `POST /v1/income/opportunities` — create a research opportunity.
- `GET /v1/income/opportunities/{id}` — read one owner-scoped opportunity.
- `POST /v1/income/opportunities/{id}/status` — perform an allowed lifecycle transition.
- `DELETE /v1/income/opportunities/{id}` — delete an owner-scoped record.

The owner identifier is derived from `auth_service.resolve_session`; a client-supplied `owner_id` is rejected as an unknown field. IDs and request payloads are bounded, query parameters are strict, and errors do not expose database exception details.

## Runtime integration

The ASGI runtime accepts an optional `SQLiteIncomeOpportunityStore`. The standard `scripts/run_web.py` profile provisions `income-opportunities.sqlite3` in the configured workspace and injects the store. The store opens SQLite connections per operation, so runtime shutdown only closes the conversation store.

## Security and product boundaries

- Browser-session auth is distinct from paired-device bearer credentials.
- Every store operation is scoped to the authenticated owner.
- The API only manages opportunity research records and their lifecycle.
- It does not search external websites, connect platform accounts, submit applications, publish listings, send messages, accept contracts or move money.
- Estimates remain estimates. Realized revenue requires a separate settlement/reconciliation ledger.
- Platform action execution remains disabled until the platform registry, policy review and durable exact-action approvals are enforced by a dedicated adapter.

## Acceptance tests

Test unauthenticated rejection, owner identity derivation, cross-owner 404 behavior, create/list/get/transition/delete, strict query and body validation, unrelated route delegation and runtime injection. The endpoint must not create a capability to execute platform actions.
