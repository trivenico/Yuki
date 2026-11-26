"""Disposable PostgreSQL least-privilege tests for API conversation persistence."""
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
class PostgresConversationStoreRoleIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        cls.admin_url = POSTGRES_URL
        cls.role = "yuki_conversation_api_" + uuid.uuid4().hex[:12]
        cls.password = uuid.uuid4().hex
        with psycopg.connect(cls.admin_url) as conn:
            for migration_name in (
                "0001_security_work_leases.sql",
                "0003_security_alert_outbox.sql",
                "0005_security_monitor.sql",
                "0016_conversation_storage.sql",
            ):
                migration = root / "migrations/security" / migration_name
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
            conn.execute("CREATE TABLE IF NOT EXISTS yuki_schema_migrations (version TEXT PRIMARY KEY)")
            conn.execute(
                "TRUNCATE conversation_generation_runs, conversation_generation_leases, "
                "conversation_messages, conversations"
            )
            conn.execute(
                "REVOKE ALL ON conversations, conversation_messages, "
                "conversation_generation_leases, conversation_generation_runs, "
                "yuki_schema_migrations, security_alert_outbox, security_monitor_incidents, "
                "security_work_leases FROM PUBLIC"
            )
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(cls.role), sql.Literal(cls.password)))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(cls.role)))

            grants = {
                "conversations": {
                    "select": "conversation_id,owner_id,title,created_at,updated_at,version,archived",
                    "insert": "conversation_id,owner_id,title,created_at,updated_at,version,archived",
                    "update": "title,updated_at,version,archived",
                    "delete": "",
                },
                "conversation_messages": {
                    "select": "message_id,conversation_id,sequence,role,content,idempotency_key,created_at",
                    "insert": "message_id,conversation_id,sequence,role,content,idempotency_key,created_at",
                    "update": "",
                    "delete": "",
                },
                "conversation_generation_leases": {
                    "select": "conversation_id,owner_id,lease_token,expires_at",
                    "insert": "conversation_id,owner_id,lease_token,expires_at",
                    "update": "owner_id,lease_token,expires_at",
                    "delete": "TABLE",
                },
                "conversation_generation_runs": {
                    "select": "generation_id,conversation_id,owner_id,request_id,user_message_id,status,checkpoint,started_at,updated_at,finished_at",
                    "insert": "generation_id,conversation_id,owner_id,request_id,user_message_id,status,checkpoint,started_at,updated_at",
                    "update": "checkpoint,updated_at,status,finished_at",
                    "delete": "",
                },
            }
            for table, privileges in grants.items():
                for privilege in ("select", "insert", "update", "delete"):
                    columns = privileges[privilege]
                    if not columns:
                        continue
                    if privilege == "delete":
                        conn.execute(sql.SQL("GRANT DELETE ON {} TO {}").format(
                            sql.Identifier(table), sql.Identifier(cls.role),
                        ))
                    else:
                        conn.execute(sql.SQL("GRANT {} ({}) ON {} TO {}").format(
                            sql.SQL(privilege.upper()),
                            sql.SQL(",").join(sql.Identifier(column.strip()) for column in columns.split(",")),
                            sql.Identifier(table),
                            sql.Identifier(cls.role),
                        ))

    @classmethod
    def tearDownClass(cls):
        if not _ENABLED:
            return
        with psycopg.connect(cls.admin_url) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(cls.role)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.role)))

    @classmethod
    def role_url(cls):
        params = conninfo_to_dict(cls.admin_url)
        params.update(user=cls.role, password=cls.password)
        return make_conninfo(**params)

    def setUp(self):
        from core.postgres_conversation_store import PostgresConversationStore
        self.store = PostgresConversationStore(self.role_url(), min_pool_size=1, max_pool_size=1)

    def tearDown(self):
        self.store.close()

    def assert_denied(self, statement):
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with psycopg.connect(self.role_url()) as conn:
                conn.execute(statement)

    def test_conversation_api_lifecycle_works_with_restricted_login_role(self):
        from core.conversation_store import ConversationConflict, ConversationNotFound

        owner = "tenant-user-" + uuid.uuid4().hex
        conversation = self.store.create_conversation(owner_id=owner)
        conversation_id = conversation["conversation_id"]
        user_message = self.store.append_message(
            owner_id=owner, conversation_id=conversation_id, role="user",
            content="Hello Yuki", idempotency_key="user-1", expected_version=0,
            generation_request_id="request-1",
        )
        self.assertTrue(user_message["created"])
        replay = self.store.append_message(
            owner_id=owner, conversation_id=conversation_id, role="user",
            content="Hello Yuki", idempotency_key="user-1", expected_version=0,
            generation_request_id="request-1",
        )
        self.assertFalse(replay["created"])
        # A known token proves lease renewal, fenced assistant persistence and release.
        token = "worker-token-" + uuid.uuid4().hex
        self.store.acquire_generation_lease(
            owner_id=owner, conversation_id=conversation_id,
            lease_token=token, ttl_seconds=60,
        )
        renewed = self.store.renew_generation_lease(
            owner_id=owner, conversation_id=conversation_id,
            lease_token=token, ttl_seconds=90,
        )
        self.assertIsNotNone(renewed)
        assistant = self.store.append_message(
            owner_id=owner, conversation_id=conversation_id, role="assistant",
            content="Hello.", idempotency_key="assistant-1",
            expected_version=1, lease_token=token,
        )
        self.assertTrue(assistant["created"])
        self.assertEqual(len(self.store.list_messages(owner_id=owner, conversation_id=conversation_id)), 2)
        self.assertTrue(self.store.update_generation_checkpoint(
            owner_id=owner, conversation_id=conversation_id, request_id="request-1",
            checkpoint={"phase": "responded"},
        ))
        self.assertTrue(self.store.finish_generation(
            owner_id=owner, conversation_id=conversation_id, request_id="request-1",
            status="completed",
        ))
        self.assertEqual(self.store.list_interrupted_generations(owner_id=owner), [])
        self.assertTrue(self.store.release_generation_lease(
            owner_id=owner, conversation_id=conversation_id, lease_token=token,
        ))
        self.assertEqual(self.store.get_conversation(
            owner_id=owner, conversation_id=conversation_id,
        )["version"], 2)
        self.store.archive_conversation(owner_id=owner, conversation_id=conversation_id)
        self.assertEqual(self.store.list_conversations(owner_id=owner), [])
        with self.assertRaises(ConversationNotFound):
            self.store.get_conversation(owner_id="other-owner", conversation_id=conversation_id)
        with self.assertRaises(ConversationConflict):
            self.store.append_message(
                owner_id=owner, conversation_id=conversation_id, role="user",
                content="Archived", idempotency_key="after-archive",
            )

    def test_conversation_role_cannot_access_security_or_migration_relations(self):
        for statement in (
            "SELECT * FROM yuki_schema_migrations",
            "SELECT * FROM security_alert_outbox",
            "SELECT * FROM security_monitor_incidents",
            "SELECT * FROM security_work_leases",
            "CREATE TABLE public.conversation_role_must_not_create (id integer)",
            "ALTER TABLE conversations ADD COLUMN unsafe text",
            "TRUNCATE conversations",
            "DELETE FROM conversations",
            "DELETE FROM conversation_messages",
            "DELETE FROM conversation_generation_runs",
            "UPDATE conversation_messages SET content='tampered'",
            "UPDATE conversation_generation_runs SET owner_id='attacker'",
        ):
            self.assert_denied(statement)

    def test_role_has_only_declared_column_privileges(self):
        expected = {
            "conversations": {
                "SELECT": {"conversation_id", "owner_id", "title", "created_at", "updated_at", "version", "archived"},
                "INSERT": {"conversation_id", "owner_id", "title", "created_at", "updated_at", "version", "archived"},
                "UPDATE": {"title", "updated_at", "version", "archived"},
                "DELETE": set(),
            },
            "conversation_messages": {
                "SELECT": {"message_id", "conversation_id", "sequence", "role", "content", "idempotency_key", "created_at"},
                "INSERT": {"message_id", "conversation_id", "sequence", "role", "content", "idempotency_key", "created_at"},
                "UPDATE": set(), "DELETE": set(),
            },
            "conversation_generation_leases": {
                "SELECT": {"conversation_id", "owner_id", "lease_token", "expires_at"},
                "INSERT": {"conversation_id", "owner_id", "lease_token", "expires_at"},
                "UPDATE": {"owner_id", "lease_token", "expires_at"},
                "DELETE": {"conversation_id", "owner_id", "lease_token", "expires_at"},
            },
            "conversation_generation_runs": {
                "SELECT": {"generation_id", "conversation_id", "owner_id", "request_id", "user_message_id", "status", "checkpoint", "started_at", "updated_at", "finished_at"},
                "INSERT": {"generation_id", "conversation_id", "owner_id", "request_id", "user_message_id", "status", "checkpoint", "started_at", "updated_at"},
                "UPDATE": {"checkpoint", "updated_at", "status", "finished_at"},
                "DELETE": set(),
            },
        }
        with psycopg.connect(self.admin_url) as conn:
            for table, privileges in expected.items():
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    actual_table_grant = conn.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (self.role, "public." + table, privilege),
                    ).fetchone()[0]
                    self.assertEqual(
                        actual_table_grant,
                        table == "conversation_generation_leases" and privilege == "DELETE",
                        (table, privilege),
                    )
                columns = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s", (table,)
                ).fetchall()
                for (column,) in columns:
                    for privilege, allowed in privileges.items():
                        if privilege == "DELETE":
                            continue
                        actual = conn.execute(
                            "SELECT has_column_privilege(%s,%s,%s,%s)",
                            (self.role, "public." + table, column, privilege),
                        ).fetchone()[0]
                        self.assertEqual(actual, column in allowed, (table, column, privilege))


if __name__ == "__main__":
    unittest.main()
