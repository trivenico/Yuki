# PostgreSQL Runtime Identity Separation

The production security bootstrap accepts six distinct PostgreSQL connection identities
through the deployment-owned `database_urls` mapping:

| Key | Repository | Intended database responsibility |
| --- | --- | --- |
| `authorization_audit` | Authorization audit log | Append authorization decisions and verify/read the audit chain |
| `workload_key_registry` | Workload key registry | Key lifecycle operations and authorized key-audit reads |
| `security_monitor` | Security event monitor | Record signals and manage incident state/history |
| `ingestion_cursors` | Security ingestion cursors | Advance fenced ingestion checkpoints |
| `alert_outbox` | Security alert outbox | Enqueue, claim, retry, and complete alert deliveries |
| `work_leases` | Security work lease store | Claim, renew, and release security pipeline leases |

## Bootstrap contract

- Every key is required, non-empty, bounded, and must identify a distinct connection
  string. Missing or reused values fail startup.
- Credentials are injected by the deployment-owned bootstrap from a secret manager;
  they must not be committed to source, checked into example environment files, or
  emitted in logs.
- The legacy single `database_url` is not accepted by this production composition.
- Pool sizing and timeout configuration remain shared operational settings; credentials
  are not shared.
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

The workload-key repository currently serves both privileged lifecycle operations and
the security ingestor's authorized key-audit reads. A future split should introduce a
separate read-only audit interface and must not grant lifecycle mutation rights to an
ingestion worker merely to read audit events.

Likewise, the authorization-audit repository is both the decision writer and the
ingestion source. Before separating API and worker service credentials, introduce and
test a read-only audit reader contract so the worker cannot append authorization
decisions. The same principle applies to monitor and outbox operations: role boundaries
must follow actual call paths, not just service labels.

## Deployment sequencing

1. Provision secret-manager entries and database LOGIN principals outside the repo.
2. Apply reviewed NOLOGIN role membership and object grants using the migration/deploy
   identity, not the API or worker.
3. Supply six distinct DSNs to the deployment-owned bootstrap.
4. Run positive and negative permission integration tests on a disposable PostgreSQL
   database.
5. Only then configure separate API and worker bootstrap identities and validate each
   process independently.
6. Keep the migration executor on its own short-lived release identity; never expose
   that credential to the API or worker.

No production credentials, grants, cloud resources, or database changes are included
in this repository change.
