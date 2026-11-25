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

## State machine

REQUESTED -> AUTHORIZED -> IDENTITY_CREATED -> MEMBERSHIP_GRANTED -> CREDENTIAL_ISSUED -> MIGRATION_RUNNING -> MIGRATION_SUCCEEDED or MIGRATION_FAILED -> CLEANUP_PENDING -> MEMBERSHIP_REVOKED -> LOGIN_DISABLED -> CREDENTIAL_REVOKED -> RETIREMENT_VERIFIED -> COMPLETED

Every non-terminal state has an expiry and is eligible for reconciliation. Any error after identity creation transitions to CLEANUP_PENDING. A cleanup failure remains visible and retryable; it must never be recorded as completed.

The migration result and credential retirement are separate facts. A migration may have succeeded while retirement failed. In that case, release promotion is blocked and the controller continues cleanup.

## Execution protocol

1. Validate protected release authorization, target database identity, immutable artifact digest, migration manifest and backup/restore evidence.
2. Acquire a durable, target-scoped deployment lease with a fencing token.

**The lease is not the final fencing boundary.** Every runner invocation must carry its monotonically increasing target generation to the protected PostgreSQL execution boundary. The target-side adapter must atomically remember the highest accepted generation and reject any lower generation before applying migration work. This protects against a paused controller or runner resuming after its lease expired and a newer controller acquired the target. A controller-side lease check alone is insufficient. Idempotent replay of the same attempt and manifest must not lower the target's fencing watermark; reusing an attempt ID with a different manifest digest must fail closed.
3. Persist REQUESTED/AUTHORIZED before creating any privileged database identity.
4. Create a uniquely named LOGIN with a random generated password and restrictive attributes. Set an explicit expiry/deadline in controller state; PostgreSQL itself does not provide a native per-role password TTL.
5. Grant only SET membership in the designated NOLOGIN owner role. Do not grant membership to the runtime role or unrelated roles.
6. Store the DSN/password in the approved secret manager and pass it to the one-shot migration process through a short-lived file/credential mount. Never pass it in argv, workflow logs, environment dumps, or source code.
7. Run --plan, verify the exact target and expected manifest, then require explicit release authorization before --apply.
8. Capture exit status and sanitized diagnostics. Stop on any migration error.
9. In a separate controller cleanup path, revoke owner-role membership, terminate sessions belonging to the temporary LOGIN, set NOLOGIN, rotate or invalidate its password, and revoke/delete the secret reference.
10. Independently query PostgreSQL and the secret manager to verify the membership is absent, role cannot authenticate, no active session remains, and the credential reference is no longer usable.
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
