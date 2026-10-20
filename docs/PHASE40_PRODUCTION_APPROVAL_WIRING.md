# Phase 40 — Production approval storage enforcement

## Audit finding

Phase 39 added a durable SQLite action approval store, but `SkillDispatcher` silently constructed an in-memory store when no store was injected. Production could therefore start with non-durable approval state despite the durable implementation existing.

## Changes

- `SkillDispatcher` now reads `YUKI_ENV`.
- When `YUKI_ENV=production`, construction fails closed unless an approval store is explicitly injected.
- Injected stores must implement `create()`, `consume()`, and `deny()`.
- Development retains the in-memory default for compatibility.
- Added tests for production fail-closed behavior, explicit SQLite injection, development behavior, and invalid store interfaces.

## Deployment requirement

Production composition must instantiate `SQLiteActionApprovalStore` with a private persistent path and pass it to `SkillDispatcher(approval_store=store)`. Keep the database out of source control and apply filesystem permissions appropriate to the service account. Do not put the database in an ephemeral container filesystem if approvals must survive restarts.

## Remaining gaps

This phase does not unify coordinator pending requests and dispatcher tokens into one transaction. The legacy `YukiCoreAgent.run_skill()` path still executes in-process skill objects, and the primary `main.py` runtime has not yet migrated all skill calls to the subprocess dispatcher. A subprocess is not an OS sandbox. These are explicit blockers, not completed work.

## Verification

Run full CI on the latest commit before marking this phase verified. PR remains draft until latest-head CI, security scans, integration/recovery coverage, and independent review pass.
