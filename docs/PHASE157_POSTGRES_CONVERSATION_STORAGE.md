# Phase 157 — PostgreSQL conversation persistence

## What changed

Yuki now has a PostgreSQL implementation of the existing conversation-store contract. The existing SQLite store remains the default and continues to support portable, local-first installs.

The PostgreSQL adapter uses:
- psycopg 3 with dictionary rows.
- A bounded psycopg connection pool (defaults: 1–10 connections).
- The same owner-scoped conversation, message, idempotency, generation lease and durable generation-run tables.
- PostgreSQL transactions for multi-statement operations.
- Parameterized queries and server-side constraints.
- A configuration-driven store factory.

## Configuration

SQLite remains the default:

```yaml
storage_backend: sqlite
workspace: ./yuki_workspace
```

For production PostgreSQL, set secrets through the deployment environment rather than committing credentials:

```bash
YUKI_STORAGE_BACKEND=postgres
YUKI_DATABASE_URL='postgresql://yuki:REDACTED@db.example.net/yuki?sslmode=verify-full'
```

Optional pool sizing is configured in YAML:

```yaml
database_pool_min: 1
database_pool_max: 10
```

Remote PostgreSQL endpoints must use TLS with `sslmode=require`, `verify-ca`, or `verify-full`. Prefer `verify-full` with a trusted CA. Loopback PostgreSQL may use a local non-TLS connection for development only.

Install the production dependencies from `requirements.txt`, which includes psycopg 3 and psycopg-pool.

## Application wiring

The embedding application creates the store once during startup:

```python
from core.config import load_config
from core.conversation_store_factory import create_conversation_store

config = load_config()
store = create_conversation_store(config)
# Inject store into ConversationService using the existing constructor.
```

Call `store.close()` during graceful application shutdown when using PostgreSQL. SQLite requires no explicit shutdown method.

## Migration and deployment safety

The PostgreSQL adapter creates its schema idempotently when initialized. It does not silently migrate an existing SQLite database. Existing installations must use a separately reviewed export/import migration before switching backends; never point the PostgreSQL adapter at a SQLite file or assume data was copied.

Before production rollout:
1. Provision a dedicated database and least-privilege application role.
2. Enable TLS certificate verification and restrict network ingress.
3. Configure encrypted, off-host backups and retention.
4. Test restore into a clean database and verify conversation counts and ownership boundaries.
5. Set pool limits against the database connection budget across all application replicas.
6. Run integration and concurrency tests against the actual PostgreSQL major version.
7. Keep SQLite as the rollback path until the PostgreSQL restore and cutover are verified.

Example backup and restore commands (run in a protected operations environment; never expose credentials in shell history):

```bash
pg_dump --format=custom --no-owner --file=yuki.dump "$YUKI_DATABASE_URL"
pg_restore --no-owner --dbname="$YUKI_RESTORE_DATABASE_URL" yuki.dump
```

## Known limitations

- The current adapter is a pooled single-database implementation, not a distributed database topology.
- Schema creation is idempotent but does not yet provide versioned migration tracking.
- Automated SQLite-to-PostgreSQL data migration is not included.
- Production acceptance still requires real PostgreSQL integration tests, failover/restore drills, load tests, and operational monitoring.
- The conversation API is dependency-injected; deployments must construct the selected store and inject it into ConversationService at startup.

This phase adds a real PostgreSQL storage implementation while being explicit about the operational work still required before claiming production readiness.
