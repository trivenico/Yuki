"""Real PostgreSQL isolation tests for the dedicated migration executor."""
from __future__ import annotations

import os
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:  # pragma: no cover
    psycopg = None
    sql = None
    conninfo_to_dict = None
    make_conninfo = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresMigrationExecutorRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suffix = uuid.uuid4().hex[:12]
        cls.admin_url = POSTGRES_URL
        cls.database = "yuki_migrate_" + suffix
        cls.migration_role = "yuki_migrator_" + suffix
        cls.runtime_role = "yuki_runtime_" + suffix
        cls.owner_role = "yuki_owner_" + suffix
        cls.migration_password = uuid.uuid4().hex
        cls.runtime_password = uuid.uuid4().hex
        cls._created_database = False
        cls._created_roles = False
        params = conninfo_to_dict(cls.admin_url)
        cls.admin_user = params.get("user")
        try:
            with psycopg.connect(cls.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(cls.migration_role), sql.Literal(cls.migration_password)))
                cls._created_roles = True
                admin.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(cls.runtime_role), sql.Literal(cls.runtime_password)))
                admin.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(cls.owner_role)))
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database)))
                cls._created_database = True
                admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                    sql.Identifier(cls.database), sql.Identifier(cls.migration_role),
                    sql.Identifier(cls.runtime_role)))
            db_params = dict(params)
            db_params.update(dbname=cls.database, user=cls.admin_user)
            cls.database_admin_url = make_conninfo(**db_params)
            with psycopg.connect(cls.database_admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("GRANT CREATE, USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(cls.owner_role)))
                admin.execute(sql.SQL("GRANT {} TO {} WITH SET TRUE").format(
                    sql.Identifier(cls.owner_role), sql.Identifier(cls.migration_role)))
            migration_params = dict(params)
            migration_params.update(dbname=cls.database, user=cls.migration_role, password=cls.migration_password)
            cls.migration_url = make_conninfo(**migration_params)
            runtime_params = dict(params)
            runtime_params.update(dbname=cls.database, user=cls.runtime_role, password=cls.runtime_password)
            cls.runtime_url = make_conninfo(**runtime_params)
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def _cleanup(cls):
        if not _ENABLED:
            return
        if getattr(cls, "_created_database", False):
            try:
                with psycopg.connect(cls.admin_url, autocommit=True) as admin:
                    admin.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                        (cls.database,),
                    )
                    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(cls.database)))
            except Exception:
                pass
        if getattr(cls, "_created_roles", False):
            try:
                with psycopg.connect(cls.admin_url, autocommit=True) as admin:
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}, {}, {}").format(
                        sql.Identifier(cls.migration_role), sql.Identifier(cls.runtime_role),
                        sql.Identifier(cls.owner_role)))
            except Exception:
                pass

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    def test_executor_applies_complete_manifest_and_runtime_cannot_mutate_it(self):
        from core.postgres_migrations import MIGRATION_FILES, migrate
        with psycopg.connect(self.migration_url, autocommit=True) as connection:
            pending = migrate(connection, apply=True, owner_role=self.owner_role)
            self.assertEqual(pending, MIGRATION_FILES)
            self.assertEqual(migrate(connection, apply=False, owner_role=self.owner_role), ())
            self.assertEqual(connection.execute("SELECT current_user").fetchone()[0], self.migration_role)
        with psycopg.connect(self.database_admin_url) as admin:
            ledger = admin.execute(
                "SELECT version, name FROM public.yuki_schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual([row[0] for row in ledger], list(range(1, len(MIGRATION_FILES) + 1)))
            self.assertEqual([row[1] for row in ledger], list(MIGRATION_FILES))
            self.assertEqual(admin.execute(
                "SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
                "WHERE c.oid='public.yuki_schema_migrations'::regclass"
            ).fetchone()[0], self.owner_role)
            self.assertTrue(admin.execute(
                "SELECT pg_has_role(%s,%s,'MEMBER')", (self.migration_role, self.owner_role)
            ).fetchone()[0])

        # The deployment bootstrap must revoke the temporary LOGIN's owner-role
        # membership after applying migrations. Ownership remains with the NOLOGIN
        # owner, while the release LOGIN cannot create or alter owned objects.
        with psycopg.connect(self.database_admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(self.owner_role), sql.Identifier(self.migration_role)
            ))

        with psycopg.connect(self.migration_url, autocommit=True) as migrator:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                migrator.execute("CREATE TABLE public.migration_must_not_create_after_apply (id integer)")

        for statement in (
            "SELECT * FROM public.yuki_schema_migrations",
            "INSERT INTO public.yuki_schema_migrations(version,name,checksum) VALUES (99,'forbidden','" + "0" * 64 + "')",
            "CREATE TABLE public.runtime_must_not_create (id integer)",
            "ALTER TABLE public.yuki_schema_migrations ADD COLUMN runtime_must_not_alter integer",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with psycopg.connect(self.runtime_url) as runtime:
                        runtime.execute(statement)

    def test_runtime_cannot_create_schema_objects_or_assume_migration_identity(self):
        denied_statements = (
            "CREATE SCHEMA runtime_must_not_create",
            "CREATE SEQUENCE public.runtime_must_not_create_seq",
            "CREATE FUNCTION public.runtime_must_not_create_fn() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
            "CREATE ROLE runtime_must_not_create_role",
            "CREATE DATABASE runtime_must_not_create_db",
            "SET ROLE " + self.migration_role,
            "SET ROLE " + self.owner_role,
        )
        for statement in denied_statements:
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with psycopg.connect(self.runtime_url, autocommit=True) as runtime:
                        runtime.execute(statement)

    def test_migration_executor_cannot_escalate_cluster_privileges(self):
        for statement in (
            "CREATE ROLE migration_must_not_create_role",
            "CREATE DATABASE migration_must_not_create_db",
            "ALTER ROLE " + self.runtime_role + " SUPERUSER",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with psycopg.connect(self.migration_url, autocommit=True) as migrator:
                        migrator.execute(statement)

    def test_role_attributes_and_schema_boundaries_are_explicit(self):
        with psycopg.connect(self.admin_url) as admin:
            for role in (self.migration_role, self.runtime_role):
                row = admin.execute(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit "
                    "FROM pg_roles WHERE rolname=%s", (role,)
                ).fetchone()
                self.assertEqual(row, (False, False, False, False, False, False), role)
            self.assertFalse(admin.execute(
                "SELECT has_schema_privilege(%s,'public','CREATE')", (self.migration_role,)
            ).fetchone()[0])
            self.assertFalse(admin.execute(
                "SELECT has_schema_privilege(%s,'public','CREATE')", (self.runtime_role,)
            ).fetchone()[0])
            # The temporary LOGIN's owner-role membership is revoked after
            # migration apply; it must not retain ledger access once retired.
            self.assertFalse(admin.execute(
                "SELECT has_table_privilege(%s,'public.yuki_schema_migrations','SELECT')",
                (self.migration_role,),
            ).fetchone()[0])
            self.assertFalse(admin.execute(
                "SELECT has_table_privilege(%s,'public.yuki_schema_migrations','SELECT')",
                (self.runtime_role,),
            ).fetchone()[0])
            self.assertFalse(admin.execute(
                "SELECT pg_has_role(%s,%s,'MEMBER')",
                (self.runtime_role, self.migration_role),
            ).fetchone()[0])
            self.assertFalse(admin.execute(
                "SELECT pg_has_role(%s,%s,'MEMBER')",
                (self.runtime_role, self.owner_role),
            ).fetchone()[0])
            self.assertFalse(admin.execute(
                "SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=%s",
                (self.owner_role,),
            ).fetchone()[0])


    def test_temporary_login_can_assume_nologin_owner_then_be_retired(self):
        """Prove the PostgreSQL primitives for a temporary migration identity."""
        suffix = uuid.uuid4().hex[:12]
        owner_role = "yuki_owner_" + suffix
        temporary_role = "yuki_release_" + suffix
        temporary_password = uuid.uuid4().hex
        object_name = "owner_probe_" + suffix
        owner_created = False
        temporary_created = False
        object_created = False
        try:
            with psycopg.connect(self.database_admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(owner_role)))
                owner_created = True
                admin.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(temporary_role), sql.Literal(temporary_password)))
                temporary_created = True
                admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(self.database), sql.Identifier(temporary_role)))
                admin.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(owner_role)))
                admin.execute(sql.SQL("GRANT {} TO {} WITH SET TRUE").format(
                    sql.Identifier(owner_role), sql.Identifier(temporary_role)))

            params = conninfo_to_dict(self.admin_url)
            params.update(dbname=self.database, user=temporary_role, password=temporary_password)
            with psycopg.connect(make_conninfo(**params), autocommit=True) as release:
                release.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner_role)))
                release.execute(sql.SQL("CREATE TABLE public.{} (id integer)").format(
                    sql.Identifier(object_name)))
                object_created = True
                release.execute("RESET ROLE")

            with psycopg.connect(self.database_admin_url) as admin:
                owner = admin.execute(
                    "SELECT r.rolname FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_catalog.pg_roles r ON r.oid=c.relowner "
                    "WHERE n.nspname='public' AND c.relname=%s", (object_name,)
                ).fetchone()
                self.assertEqual(owner[0], owner_role)

            with psycopg.connect(self.database_admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(owner_role), sql.Identifier(temporary_role)))

            with psycopg.connect(make_conninfo(**params), autocommit=True) as release:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    release.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner_role)))

            with psycopg.connect(self.database_admin_url) as admin:
                self.assertFalse(admin.execute(
                    "SELECT pg_has_role(%s,%s,'MEMBER')", (temporary_role, owner_role)
                ).fetchone()[0])
                self.assertFalse(admin.execute(
                    "SELECT rolcanlogin FROM pg_catalog.pg_roles WHERE rolname=%s",
                    (owner_role,),
                ).fetchone()[0])
        finally:
            with psycopg.connect(self.database_admin_url, autocommit=True) as admin:
                if object_created:
                    admin.execute(sql.SQL("DROP TABLE IF EXISTS public.{}").format(
                        sql.Identifier(object_name)))
                if temporary_created and owner_created:
                    admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(owner_role), sql.Identifier(temporary_role)))
                if temporary_created:
                    admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
                        sql.Identifier(self.database), sql.Identifier(temporary_role)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(
                        sql.Identifier(temporary_role)))
                if owner_created:
                    admin.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(
                        sql.Identifier(owner_role)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(
                        sql.Identifier(owner_role)))


    def test_executor_login_cannot_be_assumed_by_another_role(self):
        from core.postgres_migrations import MigrationError, migrate
        extra_member = "yuki_executor_member_" + uuid.uuid4().hex[:12]
        try:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(extra_member)))
                admin.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(self.migration_role), sql.Identifier(extra_member)))
            with psycopg.connect(self.migration_url, autocommit=True) as migrator:
                with self.assertRaisesRegex(MigrationError, "must not be assumable"):
                    migrate(migrator, apply=False, owner_role=self.owner_role)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(self.migration_role), sql.Identifier(extra_member)))
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(extra_member)))

    def test_executor_rejects_additional_login_role_membership(self):
        from core.postgres_migrations import MigrationError, migrate
        extra_role = "yuki_extra_executor_member_" + uuid.uuid4().hex[:12]
        try:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(extra_role)))
                admin.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(extra_role), sql.Identifier(self.migration_role)))
            with psycopg.connect(self.migration_url, autocommit=True) as migrator:
                with self.assertRaisesRegex(MigrationError, "only in the configured"):
                    migrate(migrator, apply=False, owner_role=self.owner_role)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(extra_role), sql.Identifier(self.migration_role)))
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(extra_role)))

    def test_executor_rejects_owner_role_granted_to_another_member(self):
        from core.postgres_migrations import MigrationError, migrate
        extra_member = "yuki_extra_owner_member_" + uuid.uuid4().hex[:12]
        try:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(extra_member)))
                admin.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(self.owner_role), sql.Identifier(extra_member)))
            with psycopg.connect(self.migration_url, autocommit=True) as migrator:
                with self.assertRaisesRegex(MigrationError, "granted only to"):
                    migrate(migrator, apply=False, owner_role=self.owner_role)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(self.owner_role), sql.Identifier(extra_member)))
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(extra_member)))

    def test_executor_rejects_login_capable_owner_role_before_set_role(self):
        from core.postgres_migrations import MigrationError, migrate
        unsafe_role = "yuki_unsafe_owner_" + uuid.uuid4().hex[:12]
        try:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(unsafe_role)))
            with psycopg.connect(self.migration_url, autocommit=True) as migrator:
                with self.assertRaisesRegex(MigrationError, "must be NOLOGIN"):
                    migrate(migrator, apply=False, owner_role=unsafe_role)
        finally:
            with psycopg.connect(self.admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(unsafe_role)))


if __name__ == "__main__":
    unittest.main()
