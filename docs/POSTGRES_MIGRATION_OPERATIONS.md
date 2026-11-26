# PostgreSQL migration operations

Yuki migrations are deployment operations, never API or worker startup work.

## Commands

Install the production storage extra, provide the database DSN through the deployment secret manager as `YUKI_DATABASE_URL`, then run:

```bash
yuki-db-migrate --plan
yuki-db-migrate --apply
```

The command requires an explicit mode. Planning acquires the same PostgreSQL advisory lock as apply, validates the migration history and reports pending files without creating the ledger or applying SQL. Apply is intended for one controlled release/deployment job, using a dedicated migration LOGIN that can temporarily `SET ROLE` to a dedicated `NOLOGIN` schema-owner role. The systemd example uses `YUKI_MIGRATION_OWNER_ROLE`; membership provisioning and revocation remain deployment-controller responsibilities. Do not place the DSN in command-line arguments, systemd unit files, notebooks, CI logs, or source control.

## Trusted schema boundary

Every migration transaction pins `search_path` to `public, pg_temp`
before executing its SQL. PostgreSQL implicitly searches `pg_catalog` ahead
of explicitly listed schemas when it is not named in the path. Keeping
`public` first ensures unqualified application DDL targets the application
schema, while `pg_temp` is explicitly last so temporary objects cannot shadow
application relations.

The public schema must be treated as a trusted deployment-owned namespace:
revoke CREATE from PUBLIC and all runtime LOGIN principals, and grant schema
and object creation only to the short-lived migration identity (or its tightly
controlled NOLOGIN migration role). Do not allow untrusted users or
application roles to create objects in public. The runner's fixed path
prevents session-level search-path drift; it does not compensate for an
untrusted public schema.

## Safety properties

- The checked-in manifest contains the ordered security migrations 0001–0016 under `migrations/security/`; SQL is packaged into the wheel.
- Each migration is SHA-256 pinned in the durable `public.yuki_schema_migrations` ledger. Renamed, edited, unknown, or gapped history fails closed.
- A session-level advisory lock serializes concurrent migration processes.
- Each SQL file and its ledger insert execute in one transaction. A failed migration rolls back and stops the sequence.
- A database with public tables but no Yuki migration ledger is rejected. The runner never guesses a baseline or marks historical SQL as applied. Existing databases require a separately reviewed, operator-approved adoption plan.
- No migration runs automatically when the API or worker starts.

## Operational rollout

1. Take and verify a recoverable database backup.
2. Run `--plan` against the intended database and retain the output with the release record.
3. Confirm the target database, role, backup, migration diff, and release are approved.
4. Run `--apply` once from the protected deployment job.
5. Verify the ledger, service readiness, audit-chain integrity, and application smoke tests before promoting traffic.
6. If a migration fails, stop deployment and investigate. Do not manually insert ledger rows or edit an already released migration; add a forward-only migration after review.
7. Restore from backup only under the incident recovery procedure; do not assume every future migration is safely reversible.

The initial runner is deliberately strict and targets the checked-in security schema only. Expanding it to additional database domains requires adding their complete migration inventory and tests before registering them.
