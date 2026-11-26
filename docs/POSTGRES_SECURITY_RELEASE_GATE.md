# PostgreSQL Security Release Gate

This checklist is the release boundary for the PostgreSQL authorization and audit-writer security work. Passing unit or repository tests is necessary but does not, by itself, authorize production deployment.

## Required before merge

- [ ] The complete required GitHub Actions checks have completed successfully on the exact pull-request head.
- [ ] PostgreSQL integration tests run against a supported PostgreSQL version and exercise the configured least-privilege roles.
- [ ] The migration and runtime role matrix has been reviewed against the actual deployment grants.
- [ ] Audit events remain append-only for the runtime writer; tests verify denied update/delete operations using the runtime role.
- [ ] Hash-chain verification detects modification, deletion, reordering, and malformed links in the witnessed event history.
- [ ] Checkpoint verification uses an independently controlled trust boundary; a database-local hash chain alone is not an independent integrity anchor.
- [ ] Authorization-decision evidence is verified by a concrete verifier and recorded through the production runtime composition.
- [ ] Failure of the audit/evidence path fails closed for consequential actions.
- [ ] Operational alerting, checkpoint publication/reconciliation, key rotation, and recovery procedures have named owners and tested runbooks.

## Required before a private VPS pilot

- [ ] A deployable composition root wires the production verifier, evidence recorder, checkpoint publisher, key resolver, and durable receipt source.
- [ ] The checkpoint receipt is stored and verified outside the database trust boundary it is intended to protect.
- [ ] A writer-compromise exercise demonstrates that an attacker with database-writer privileges cannot silently rewrite previously witnessed history.
- [ ] The pilot uses dedicated non-superuser runtime and migration identities; credentials are not shared with the application owner or deployment operator.
- [ ] Backup restoration and audit-chain verification are exercised together.
- [ ] The service is not enabled for consequential unattended actions until all gates above are satisfied.

## CI interruption policy

A runner shutdown, cancellation, or lost log artifact is an inconclusive result, not a passing test and not evidence of a product-code regression. Retry the affected job only after the parent workflow permits it. Record the exact commit SHA and run/job identifiers. Do not merge based on results from an earlier head.

## Current implementation boundary

The repository contains implementation components and test coverage for append-only audit writing, hash-chain verification, checkpoint/evidence contracts, and runtime guards. These components do not prove that an independent external anchor, production key resolver, durable receipt source, production verifier, alerting system, or deployable production composition is configured. Treat those as outstanding until verified in the target environment.

## Decision rule

Keep the pull request in draft and do not merge or deploy while any required gate is unchecked. A green focused test suite does not override an incomplete production trust boundary.
