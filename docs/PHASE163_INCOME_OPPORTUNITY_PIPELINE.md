# Phase 163 — Persistent Income Opportunity Pipeline

## Objective

Turn income research into a durable, owner-scoped workflow. Yuki needs to remember which opportunities it has found, what evidence supports them, what it expects them to cost, which assumptions are unverified, and what stage each opportunity has reached.

## Implemented foundation

`core/income_opportunities.py` provides a SQLite-backed store for:
- owner-scoped opportunity records;
- platform ID, category, title, description and HTTPS source reference;
- evidence classification: unverified, anecdotal, multiple sources or official;
- optional owner-entered estimated net amount, currency, startup cost and time estimate;
- optional 0–100 fit, demand and competition assessments;
- explicit lifecycle: discovered → researching → validated → ready → active → completed, with supported pause/rejection transitions;
- owner-scoped lookup, filtering, bounded listing and deletion.

The store is a persistence primitive. It is not yet wired to browser UI, the web API, CLI, autonomous discovery, platform OAuth or payout reconciliation.

## Integrity and security

- Owner ID is supplied by the trusted service layer, never taken from a public request body.
- Every read, transition and deletion is owner-scoped.
- SQLite write transitions are transactional.
- Capacity is bounded per owner as an operational safety control, not a daily or monthly earning quota.
- Source references must use HTTPS and cannot embed credentials or nonstandard ports.
- Money values are integer minor units with explicit ISO-style currency codes.
- Estimated amounts are explicitly planning estimates, not realized revenue.
- Terminal rejected/completed opportunities cannot be reopened through ordinary lifecycle transitions.
- No platform action, application, proposal, message, listing, contract or payment is performed.

## Next integration milestones

1. Authenticated API endpoints with owner identity derived from the browser session.
2. UI pipeline with evidence, status, assumptions, costs and approval state visible.
3. Opportunity discovery connectors, initially read-only and limited to documented platform access.
4. Policy freshness checks against the Phase 162 platform registry before enabling any adapter.
5. Durable approval-bound external actions and immutable audit records.
6. Separate revenue-event ledger for reconciled settlements, fees, refunds and actual net receipts.
7. Portfolio ranking that displays assumptions and uncertainty rather than promising earnings.

## Acceptance tests

The store must preserve data across restart, prevent cross-owner reads and writes, reject malformed monetary values and unsafe source URLs, enforce lifecycle transitions, enforce bounded capacity and support deterministic filtering. The first PR adds unit coverage for these properties; integration into the authenticated runtime is a separate milestone.
