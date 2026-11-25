# PostgreSQL Migration Credential Lifecycle Controller

Status: architecture and implementation contract; **not an executable provisioner**.

This document defines the next deployment control-plane component for the private Oracle Cloud Linux pilot. It deliberately does not create database roles, passwords, grants, secrets, or cloud resources. The secret-manager provider, database role bootstrap authority, deployment runner, and OCI region have not yet been selected/configured.

## Security objective

A migration LOGIN is an ephemeral deployment credential. It may assume one dedicated NOLOGIN schema-owner role only for one approved release operation. It must never be available to the API, worker, Kaggle notebook, developer shell, or ordinary CI job.

The migration runner's RESET ROLE and normal-process cleanup are useful session protections, but they are not a crash-recovery mechanism. The controller must independently retire the LOGIN and revoke its owner-role membership even if the runner exits abnormally or the controller process is terminated.

## Required identities

- **Bootstrap/controller identity:** separately controlled authority allowed to create/alter/drop only the ephemeral migration LOGIN and grant/revoke its membership in the one migration owner role. It is not the application runtime identity and is not supplied to the migration process.
- **Migration LOGIN:** unique per release attempt, short-lived, NOINHERIT, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS.
- **Schema owner:** stable NOLOGIN role, NOINHERIT, with ownership of migration-created objects and only the required schema privileges.
- **Runtime LOGINs:** cannot SET ROLE to either privileged identity and have no DDL or migration-ledger privileges.

Do not grant a temporary LOGIN membership in broad administrator roles. PostgreSQL membership must explicitly permit SET for the intended owner role; INHERIT must remain disabled. Revoke that membership before disabling/dropping the temporary LOGIN.

## Durable attempt record

Before issuing a credential, persist a controller-owned attempt record in a control-plane store separate from the application database being migrated. The record must include:

- unique attempt ID and unique temporary role name;
- a cluster-global uniqueness constraint binding each temporary PostgreSQL role name to exactly one attempt; a role-name collision across attempts must fail closed before any membership grant;
- an immutable binding from each attempt to exactly one designated NOLOGIN schema-owner role; retries may repeat that same grant, but attempts must not accumulate membership in alternate owner roles;
- release commit, immutable artifact digest, target database identity and migration manifest digest;
- approved operator/release authorization reference;
- created-at, expiry/deadline, heartbeat and terminal timestamps;
- state and monotonically increasing state version;
- credential reference (secret-manager object ID only; never secret bytes);

Credential issuance must be idempotent by migration attempt ID, and every issued secret
must be discoverable by that attempt ID until retirement is independently verified.
This closes the crash window where the secret manager successfully issues a credential
but the controller crashes before the opaque reference is committed to the attempt store.
During reconciliation, call an attempt-scoped revoke operation even when the durable
attempt has no credential reference; the adapter must discover and revoke any orphaned
issuance rather than interpreting a missing reference as proof that no credential exists.
- last sanitized failure category and cleanup verification evidence.

Never put DSNs, passwords, tokens, private keys, or secret-manager payloads in the record, logs, GitHub Actions outputs, or audit event bodies.

The record store must support conditional state transitions/CAS and durable reconciliation scans. A process-local lock or an in-memory cleanup handler is not sufficient.

Recovery scans must use stable keyset pagination ordered by `(updated_at, attempt_id)`, with a strict cursor predicate applied in the same database query as the page limit. Offset pagination is not sufficient for a changing queue, and repeatedly fetching only the first bounded page can permanently starve later attempts when early records remain stuck. The reconciler must persist its last completed cursor or safely restart from the beginning while ensuring every page can be traversed; retries for an individual failed attempt must not block progress to later candidates. Production SQL adapters must test equal timestamps, concurrent state updates, cursor continuation and bounded query plans.

## State machine

REQUESTED -> AUTHORIZED -> IDENTITY_CREATED -> MEMBERSHIP_GRANTED -> CREDENTIAL_ISSUED -> MIGRATION_RUNNING -> MIGRATION_SUCCEEDED or MIGRATION_FAILED -> CLEANUP_PENDING -> MEMBERSHIP_REVOKED -> LOGIN_DISABLED -> CREDENTIAL_REVOKED -> RETIREMENT_VERIFIED -> COMPLETED

Every non-terminal state has an expiry and is eligible for reconciliation. Any error after identity creation transitions to CLEANUP_PENDING. A cleanup failure remains visible and retryable; it must never be recorded as completed.

The migration result and credential retirement are separate facts. A migration may have succeeded while retirement failed. In that case, release promotion is blocked and the controller continues cleanup.

## Execution protocol

1. Validate protected release authorization, target database identity, immutable artifact digest, migration manifest and backup/restore evidence.
2. Acquire a durable, target-scoped deployment lease with a fencing token.

**The lease is not the final fencing boundary.** Every runner invocation must carry its monotonically increasing target generation to the protected PostgreSQL execution boundary. The target-side adapter must atomically remember the highest accepted generation and reject any lower generation before applying migration work. This protects against a paused controller or runner resuming after its lease expired and a newer controller acquired the target. A controller-side lease check alone is insufficient. Idempotent replay of the same attempt and manifest must not lower the target's fencing watermark; reusing an attempt ID with a different manifest digest must fail closed.
3. Persist REQUESTED/AUTHORIZED before creating any privileged database identity.
4. Create a uniquely named LOGIN with a random generated password and restrictive attributes. Generate role identifiers from a narrow allowlist (for example, lowercase ASCII letters, digits and underscores, with PostgreSQL's 63-byte identifier limit); verify uniqueness against the cluster-global `pg_roles` namespace and reject collisions with every reserved owner, service or migration role. Still use the driver's identifier-quoting API for every DDL statement and never interpolate caller-supplied names. Reject a LOGIN name equal to its owner role. Set an explicit expiry/deadline in controller state; PostgreSQL itself does not provide a native per-role password TTL. Allocation must also be race-safe: a catalog precheck is advisory, not a uniqueness guarantee. Serialize name allocation within the identity-administration boundary and treat PostgreSQL's duplicate-role error as a conflict; never retry by silently adopting an existing role. The cluster-global role catalog remains the source of truth.
5. Before granting membership, independently verify the designated owner role is an approved schema-owner identity with NOLOGIN and without SUPERUSER, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS. Reject unregistered owner roles and any attribute drift. Grant only the minimum required SET membership in that exact NOLOGIN owner role; do not grant membership to the runtime role or unrelated roles.
6. Store the DSN/password in the approved secret manager and pass it to the one-shot migration process through a short-lived file/credential mount. Never pass it in argv, workflow logs, environment dumps, or source code.
7. Run --plan, verify the exact target and expected manifest, then require explicit release authorization before --apply.
8. Capture exit status and sanitized diagnostics. Stop on any migration error.
9. In a separate controller cleanup path, revoke membership only from the exact owner role durably bound to the attempt; reject a cleanup request naming a different owner role rather than silently treating it as success. Then terminate sessions belonging to the temporary LOGIN, set NOLOGIN, rotate or invalidate its password, and revoke/delete the secret reference.
10. Independently query PostgreSQL and the secret manager to verify the temporary LOGIN has no remaining role memberships (not merely that the expected owner membership is absent), cannot authenticate, has no active session, and the credential reference is no longer usable. Any unexpected membership or catalog drift keeps retirement pending.
11. Mark RETIREMENT_VERIFIED only after all independent checks succeed. Otherwise keep CLEANUP_PENDING, alert, and block release promotion.

Use bounded retries with backoff for cleanup operations. Operations must be idempotent: revoking absent membership, disabling an already-disabled role, terminating zero sessions, and revoking an already-revoked secret must be safe. Do not suppress cleanup errors or report success when only some steps succeeded.

## Independent crash recovery

A separately supervised reconciler must periodically scan expired and non-terminal attempts. It must not depend on the migration runner or the controller process that created the attempt.

For each attempt it must acquire a fenced reconciliation lease; re-read durable state and database role identity; revoke owner-role membership; terminate remaining sessions; set NOLOGIN and invalidate the password; invoke attempt-scoped secret retirement (including orphaned issuance when no reference was committed); verify the resulting state from independent queries; append a durable audit event; and transition the attempt to COMPLETED only after all retirement checks pass.

If the controller database is unavailable, the reconciler must alert and fail closed. If the target database is unavailable, do not assume cleanup occurred: retain CLEANUP_PENDING, retry, and block new release attempts for that target until policy-approved recovery. Reconciliation must use a separately stored, least-privilege bootstrap credential with audited access.

The domain contract validates every audit envelope's claimed state edge against the same allowed-transition graph used by the attempt model. An initial event may only establish `REQUESTED`; subsequent events cannot claim skipped or otherwise forbidden transitions. This prevents an adapter or caller from producing a syntactically valid but impossible lifecycle history. Durable ordering, append-only storage, independent read-back and actor authentication remain adapter responsibilities.

## Audit and observability

Emit append-only events for authorization, identity creation, membership grant/revoke, secret issue/revoke, migration start/result, cleanup attempt, verification and reconciliation. Events must contain attempt ID, role name, target identity, artifact/manifest digests, actor identity, timestamps and sanitized outcome. Never log secret material or raw DSNs.

Alert on attempts past expiry; membership remaining after migration completion; LOGIN left enabled past its deadline; cleanup/reconciliation errors; credential references that cannot be revoked or verified; concurrent or stale deployment leases; and mismatch between expected and observed role attributes/membership.

## Required tests before implementation is production-eligible

Use disposable PostgreSQL 16 and a fake secret-manager adapter first, then provider-specific integration tests.

1. Happy path: apply, revoke membership, disable LOGIN, revoke secret, verify.
2. Migration failure: all cleanup steps still run; failure remains recorded.
3. Crash after each state transition: reconciler completes retirement.
4. Crash after membership grant but before secret issue: role is retired.
5. Crash after secret issue but before runner start: role and secret are retired.
6. Database outage during cleanup: state remains pending; no false success.
7. Secret-manager outage: promotion is blocked and reconciliation retries.
8. Repeated reconciliation: idempotent, no privilege restoration.
9. Concurrent attempts: target-scoped fencing prevents overlapping migration.
10. Role-name/SQL injection attempts: identifiers remain safely quoted.
11. Verify no runtime role can assume the owner or bootstrap role.
12. Verify audit records never contain passwords, DSNs or credential payloads.
13. Verify controller and reconciler use different execution identities.
14. Verify successful migration with failed cleanup is not promotable.

## Pilot rollout gate

Do not deploy this controller until the following are selected and reviewed: secret manager, durable controller state store, bootstrap identity model, network path to PostgreSQL, alert destination, release authorization policy, backup/restore procedure, and OCI deployment runner.

For the first pilot, keep the Yuki application private on one Linux VPS. GitHub Actions may build and attest the immutable release artifact, but a GitHub-hosted runner must not be given a route into a private OCI subnet. Use a reviewed private runner or a pull-based deployment agent with short-lived authorization. Never expose PostgreSQL or SSH publicly to make deployment convenient.

This contract is a design gate, not evidence that a controller, reconciler, secret-manager integration, or production deployment already exists.


## Credential binding domain contract

The provider-neutral domain now defines an immutable `MigrationCredentialBinding` containing only attempt ID, target ID, PostgreSQL LOGIN name, opaque secret reference, credential version, and binding timestamp. It rejects malformed PostgreSQL role identifiers, unbounded/empty references, embedded NULs, and naive timestamps. It has no password or secret-value field.

The new `MigrationCredentialProvisioner` protocol defines the trusted broker boundary: bind a secret version to the exact LOGIN, independently verify the binding, retire one binding, discover and retire all attempt-scoped credentials after a crash, and independently verify retirement. The `BoundMigrationRunner` protocol accepts this binding metadata and resolves the secret only inside its one-shot execution boundary.

These are domain contracts only. No production broker, Vault adapter, PostgreSQL identity adapter, or bound runner has been implemented. The existing controller and legacy runner are not silently switched to these interfaces. Adapter integration, durable binding storage, cross-system crash recovery, real PostgreSQL tests, and secret-manager integration remain required before production use.

## Adapter implementation gate: credential-to-role binding

The current provider-neutral protocols are not yet sufficient to implement the documented temporary-LOGIN provisioning flow safely:

- `MigrationSecretManager.issue(attempt_id, target_id)` returns only an opaque reference. It does not define how a random database password is generated, bound to a specific PostgreSQL LOGIN, or delivered to the role without exposing the password to orchestration code.
- `MigrationIdentityAdmin.create_temporary_login(attempt_id, role_name)` creates a LOGIN but accepts neither a credential reference nor a narrowly scoped password-provisioning capability. The interface therefore cannot prove that the credential stored by the secret manager authenticates as the exact LOGIN bound to the attempt.
- `MigrationRunner.execute` receives the secret reference, but the protocol does not define an authenticated binding check between that reference, the attempt, the target endpoint, and the PostgreSQL role before connection.

Do not implement a production identity adapter by generating a password in general controller code, returning it through the domain layer, or reusing the application's ordinary database connection. That would violate the secret boundary described above.

Before a production adapter is written, introduce a reviewed credential-provisioning boundary with these properties:

1. A dedicated credential issuer generates high-entropy password material inside its trusted boundary and binds it atomically/idempotently to the attempt ID, target identity, and exact generated LOGIN name.
2. A separate identity-admin capability applies that credential to the exact LOGIN using parameter-safe PostgreSQL operations, without returning password material to the controller. The capability must verify the attempt-to-role binding before changing credentials.
3. The secret manager returns only an opaque, versioned reference. The runner resolves it only inside its one-shot execution boundary and verifies its immutable attempt/target/role binding before connecting.
4. Retry and crash recovery are explicit: repeated issuance for the same attempt and exact target/role returns the same active version or a conflict; a different binding conflicts; orphaned issuance is discoverable and revocable by attempt ID.
5. Independent verification proves both sides of the binding: the PostgreSQL role exists with the required attributes and the secret reference resolves to credentials authorized for that exact role/target. No secret bytes appear in attempt state, audit events, workflow outputs, logs, or model context.

The interface review must also decide whether password rotation/invalidation is performed by the identity-admin adapter, the credential issuer, or a tightly coupled operation spanning both. It must not claim retirement until PostgreSQL authentication is disabled and the external secret version is independently confirmed unusable.

Until this contract is designed and tested, the existing in-memory identity-admin fake remains test-only. Existing PostgreSQL integration tests prove individual PostgreSQL role/membership primitives; they do not prove end-to-end secret issuance, role binding, or production identity-adapter behavior.
