# Phase 158 — Application runtime wiring

## Runtime composition

`api.runtime.build_conversation_runtime` composes the authenticated conversation API with the configured storage backend and the guarded agent execution loop. The embedding application supplies the already-configured `BuiltInAuthService` and `AgentExecutionLoop`; this keeps provider credentials, account bootstrap and tool authorization outside request-controlled configuration.

```python
from api.runtime import build_conversation_runtime
from core.auth import BuiltInAuthService
from core.config import load_config

config = load_config()
auth = BuiltInAuthService(f"{config['workspace']}/auth.sqlite3")
# Construct `agent_loop` with the selected provider and guarded tool protocol.
runtime = build_conversation_runtime(
    config, auth_service=auth, agent_loop=agent_loop,
)
app = runtime  # ASGI callable
```

The runtime supports ASGI HTTP and lifespan scopes. On `lifespan.shutdown`, it closes the selected conversation store exactly once. SQLite remains the default; PostgreSQL is selected by `YUKI_STORAGE_BACKEND=postgres` and `YUKI_DATABASE_URL`.

## PostgreSQL SQL compatibility correction

The shared conversation-store contract uses SQLite integer literals for archived-state predicates. PostgreSQL uses a strict BOOLEAN column, so the adapter now translates those predicates to `TRUE`/`FALSE` in addition to translating qmark placeholders and `BEGIN IMMEDIATE`. Regression tests cover the generated SQL.

## Scope and production boundary

This composes the existing API and storage into a reusable ASGI runtime. It does not add a public internet deployment, account-registration flow, TLS termination, secrets manager, or production provider/tool policy. The deployment must still provide HTTPS at the edge, rate limiting, secure bootstrap and credential management, a guarded agent loop, and database backup/restore procedures. PostgreSQL behavior must be exercised against a real database using `YUKI_TEST_POSTGRES_URL` before production cutover.
