"""Explicit, locked PostgreSQL migration runner for deployment use only.

This module is intentionally not called by API or worker startup. It refuses to
adopt a non-empty database without an existing Yuki migration ledger; operators
must perform a separately reviewed baseline/adoption procedure for such DBs.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Iterable

MIGRATION_PACKAGE = "migrations"
MIGRATION_FILES = (
    "0001_security_work_leases.sql",
    "0002_security_ingestion_cursors.sql",
    "0003_security_alert_outbox.sql",
    "0004_workload_key_registry.sql",
    "0005_security_monitor.sql",
    "0006_authorization_audit.sql",
    "0007_workflow_runs.sql",
    "0008_workload_delegations.sql",
    "0009_external_operations.sql",
    "0010_workflow_task_ownership.sql",
    "0011_external_operation_authorization_context.sql",
    "0012_agent_plan_repository.sql",
    "0013_agent_plan_outbox_claim_fencing.sql",
    "0014_agent_plan_subtask_action.sql",
    "0015_agent_plan_dispatch_cancellation.sql",
    "0016_conversation_storage.sql",
    "0017_specialist_agent_registry.sql",
)
LEDGER_TABLE = "public.yuki_schema_migrations"
# Stable, application-specific two-key advisory lock namespace.
LOCK_KEY_1 = 0x59554B49  # "YUKI"
LOCK_KEY_2 = 1


class MigrationError(RuntimeError):
    """Raised when migration state is unsafe or inconsistent."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    checksum: str


def load_migrations() -> tuple[Migration, ...]:
    root = files(MIGRATION_PACKAGE).joinpath("security")
    registered = set(MIGRATION_FILES)
    discovered = {item.name for item in root.iterdir() if item.is_file() and item.name.endswith(".sql")}
    if discovered != registered:
        missing = sorted(discovered - registered)
        absent = sorted(registered - discovered)
        raise MigrationError(f"Migration manifest mismatch; unregistered={missing}, missing={absent}")
    result = []
    for expected_version, name in enumerate(MIGRATION_FILES, start=1):
        if not name.startswith(f"{expected_version:04d}_") or not name.endswith(".sql"):
            raise MigrationError(f"Invalid migration manifest entry: {name}")
        try:
            sql = root.joinpath(name).read_text(encoding="utf-8")
        except OSError as exc:
            raise MigrationError(f"Missing packaged migration {name}") from exc
        if not sql.strip():
            raise MigrationError(f"Migration is empty: {name}")
        result.append(Migration(expected_version, name, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    return tuple(result)


def _fetch_applied(cursor: Any) -> dict[int, tuple[str, str]]:
    cursor.execute(f"SELECT version, name, checksum FROM {LEDGER_TABLE} ORDER BY version")
    return {int(version): (str(name), str(checksum)) for version, name, checksum in cursor.fetchall()}


def _ensure_safe_initialization(cursor: Any) -> None:
    cursor.execute("SELECT to_regclass(%s)", (LEDGER_TABLE,))
    if cursor.fetchone()[0] is not None:
        return
    # No implicit baseline: existing application tables may represent a manually
    # migrated or partially migrated database whose history cannot be proven.
    cursor.execute(
        "SELECT c.relname FROM pg_catalog.pg_class AS c "
        "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
        "ORDER BY c.relname"
    )
    existing = [row[0] for row in cursor.fetchall()]
    if existing:
        raise MigrationError(
            "Refusing to initialize a migration ledger in a non-empty public schema. "
            "Use a reviewed, operator-approved baseline/adoption procedure."
        )


def _ensure_ledger(cursor: Any) -> None:
    cursor.execute(
        f"""CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL CHECK (checksum ~ '^[0-9a-f]{{64}}$'),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )"""
    )


def _validate_history(applied: dict[int, tuple[str, str]], migrations: Iterable[Migration]) -> None:
    known = {item.version: item for item in migrations}
    unknown = sorted(set(applied) - set(known))
    if unknown:
        raise MigrationError(f"Database contains unknown migration versions: {unknown}")
    for version, (name, checksum) in applied.items():
        item = known[version]
        if name != item.name or checksum != item.checksum:
            raise MigrationError(
                f"Migration history mismatch at version {version}; refusing modified or renamed SQL"
            )
    versions = sorted(applied)
    if versions and versions != list(range(1, versions[-1] + 1)):
        raise MigrationError("Migration ledger has a gap; refusing out-of-order history")


def _validate_executor_role(cursor: Any) -> None:
    """Require the connected session identity to be a dedicated, non-escalating LOGIN."""
    cursor.execute(
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname = session_user"
    )
    attributes = cursor.fetchone()
    if attributes is None or not attributes[0]:
        raise MigrationError("Migration executor must authenticate as a LOGIN role")
    if any(attributes[1:]):
        raise MigrationError("Migration executor LOGIN role must not have cluster-level escalation attributes")


def _validate_owner_role(cursor: Any, owner_role: str) -> None:
    """Require a non-login, non-escalating schema-owner role before SET ROLE."""
    cursor.execute(
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
        "rolreplication, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=%s",
        (owner_role,),
    )
    attributes = cursor.fetchone()
    if attributes is None:
        raise MigrationError("Migration owner role does not exist")
    if any(attributes):
        raise MigrationError(
            "Migration owner role must be NOLOGIN and have no cluster-level escalation attributes"
        )


def _reset_role_or_close_connection(cursor: Any, connection: Any) -> None:
    """Never return a session whose privileged role could not be reset."""
    try:
        cursor.execute("RESET ROLE")
    except BaseException:
        # RESET ROLE can fail while the server-side session is still alive.
        # Closing the connection is the fail-closed fallback: do not hand a
        # possibly privileged session back to a caller for reuse.
        try:
            connection.close()
        except BaseException:
            pass
        raise


def migrate(connection: Any, *, apply: bool = False, owner_role: str | None = None) -> tuple[str, ...]:
    """Return pending migration names; apply only when explicitly requested.

    The caller supplies a dedicated connection with autocommit enabled so the
    session advisory lock survives the per-migration transactions.
    """
    migrations = load_migrations()
    cursor = connection.cursor()
    locked = False
    role_assumed = False
    try:
        if owner_role is not None:
            _validate_executor_role(cursor)
            if not owner_role or "\x00" in owner_role:
                raise MigrationError("Invalid migration owner role")
            try:
                from psycopg import sql
            except ImportError as exc:
                raise MigrationError("Owner-role migrations require psycopg 3") from exc
            _validate_owner_role(cursor, owner_role)
            cursor.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner_role)))
            role_assumed = True
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", (LOCK_KEY_1, LOCK_KEY_2))
        locked = True
        _ensure_safe_initialization(cursor)
        cursor.execute("SELECT to_regclass(%s)", (LEDGER_TABLE,))
        ledger_exists = cursor.fetchone()[0] is not None
        if apply and not ledger_exists:
            with connection.transaction():
                with connection.cursor() as tx:
                    _ensure_ledger(tx)
        if ledger_exists:
            applied = _fetch_applied(cursor)
        else:
            applied = {}
        _validate_history(applied, migrations)
        pending = tuple(item for item in migrations if item.version not in applied)
        if not apply:
            return tuple(item.name for item in pending)
        for item in pending:
            # DDL and its ledger record commit atomically. Any failure aborts this
            # migration and stops the sequence; no later version is attempted.
            with connection.transaction():
                with connection.cursor() as tx:
                    # Migration SQL uses unqualified object names. Pin resolution
                    # to the trusted application schema and system catalog, with
                    # pg_temp last so temporary objects cannot shadow relations.
                    tx.execute("SET LOCAL search_path = pg_catalog, public, pg_temp")
                    tx.execute(item.sql)
                    tx.execute(
                        f"INSERT INTO {LEDGER_TABLE}(version, name, checksum) VALUES (%s, %s, %s)",
                        (item.version, item.name, item.checksum),
                    )
        return tuple(item.name for item in pending)
    finally:
        try:
            if locked:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_KEY_1, LOCK_KEY_2))
        finally:
            try:
                if role_assumed:
                    _reset_role_or_close_connection(cursor, connection)
            finally:
                cursor.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or explicitly apply Yuki PostgreSQL migrations.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="show pending migrations without applying them")
    mode.add_argument("--apply", action="store_true", help="apply pending migrations")
    args = parser.parse_args(argv)
    owner_role = os.environ.get("YUKI_MIGRATION_OWNER_ROLE", "").strip() or None
    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    environment_dsn = os.environ.get("YUKI_DATABASE_URL", "").strip()
    if credentials_directory:
        if environment_dsn:
            parser.error("database credential must come from one source only")
        credential_path = Path(credentials_directory) / "database-url"
        try:
            dsn = credential_path.read_text(encoding="utf-8").strip()
        except OSError:
            parser.error("systemd database-url credential is unavailable")
        if not dsn or len(dsn) > 4096 or "\x00" in dsn:
            parser.error("systemd database-url credential is invalid")
    else:
        # Environment-based DSNs remain available for CI and controlled
        # operator tooling. Production systemd units use LoadCredential=.
        dsn = environment_dsn
    if not dsn:
        parser.error("database credential must be supplied by the deployment secret manager")
    try:
        import psycopg
    except ImportError:
        print("PostgreSQL migrations require the 'production-storage' extra (psycopg 3).", file=sys.stderr)
        return 2
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            changed = migrate(connection, apply=args.apply, owner_role=owner_role)
    except Exception as exc:
        # Driver exceptions may embed the DSN or other connection material.
        # Keep the exception class for diagnostics, but never print its message.
        print(f"Migration {'apply' if args.apply else 'plan'} failed: {type(exc).__name__}; details redacted", file=sys.stderr)
        return 1
    if not changed:
        print("Database schema is up to date.")
    else:
        print(("Applied" if args.apply else "Pending") + " migrations:")
        for name in changed:
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
