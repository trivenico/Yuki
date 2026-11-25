# PostgreSQL Runtime Identity Separation

The production security bootstrap accepts eight distinct PostgreSQL connection identities
through the deployment-owned `database_urls` mapping:

| Key | Repository | Intended database responsibility |
| --- | --- | --- |
| `authorization_audit` | Authorization audit writer | Append decisions and verify the chain |
| `authorization_audit_reader` | Authorization audit reader | Read bounded, minimized event pages for ingestion only |
| `workload_key_registry` | Workload key registry | Resolve public keys and perform authorized lifecycle operations |
| `workload_key_audit_reader` | Workload key audit reader | Read bounded lifecycle audit pages for ingestion only |
| `security_monitor` | Security event monitor | Record signals and manage incident state/history |
| `ingestion_cursors` | Security ingestion cursors | Advance fenced ingestion checkpoints |
| `alert_outbox` | Security alert outbox | Enqueue, claim, retry, and complete alert deliveries |
| `work_leases` | Security work lease store | Claim, renew, and release security pipeline leases |

## Bootstrap contract

- Every key is required, non-empty, bounded, and must identify a distinct connection
  string. Missing or reused values fail startup. The runtime now requires eight identities.
- Credentials are injected by the deployment-owned bootstrap from a secret manager;
  they must not be committed to source, checked into example environment files, or
  emitted in logs.
- The legacy single `database_url` is not accepted by this production composition.
- Pool sizing and timeout configuration remain shared operational settings. The
  bootstrap removes the complete DSN map before invoking each repository factory and
  passes only that repository's own DSN, preventing accidental credential fan-out.
- Startup remains all-or-nothing. If any repository fails readiness, every previously
  created pool is closed in reverse order.

## Important authorization and grant boundary

This change separates connection identities at repository construction. It does not
create PostgreSQL roles, issue grants, or prove that a role is least-privileged.
Those operations require a reviewed grant matrix derived from every SQL statement,
including advisory locks, sequence access, row-level operations, and readiness checks.
Use NOLOGIN group roles and distinct LOGIN principals in the deployment, then verify
both allowed and denied operations with integration tests against a disposable
PostgreSQL instance.

The runtime now constructs separate narrow reader objects for authorization and
workload-key audit ingestion. They expose only bounded page reads and schema readiness;
they do not expose append, key lifecycle, key resolution, or checkpoint publication
methods. Each reader executes queries in explicit read-only transactions. This is an
application-level boundary, not a substitute for PostgreSQL SELECT-only grants.

The writer and lifecycle repositories remain separate from these reader objects.
Deployment must provision reader LOGIN principals with SELECT-only grants on the exact
audit tables and minimum schema usage, then verify denied writes with disposable-database
integration tests. Do not grant ingestion workers writer or key-administration
credentials. Monitor and outbox role boundaries still require SQL-derived review of
their actual call paths.

## Deployment sequencing

1. Provision secret-manager entries and database LOGIN principals outside the repo.
2. Apply reviewed NOLOGIN role membership and object grants using the migration/deploy
   identity, not the API or worker.
3. Supply eight distinct DSNs to the deployment-owned bootstrap.
4. Run positive and negative permission integration tests on a disposable PostgreSQL
   database.
5. Only then configure separate API and worker bootstrap identities and validate each
   process independently.
6. Keep the migration executor on its own short-lived release identity; never expose
   that credential to the API or worker.

No production credentials, grants, cloud resources, or database changes are included
in this repository change.
