import os
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config
from core.conversation_store_factory import create_conversation_store
from core.conversation_store import ConversationStore
from core.postgres_conversation_store import PostgresConversationStore


class ConversationStoreFactoryTests(unittest.TestCase):
    def test_sqlite_remains_default_and_factory_creates_local_store(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("workspace: " + directory + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"YUKI_STORAGE_BACKEND": "sqlite"}, clear=False):
                config = load_config(config_path)
            self.assertEqual(config["storage_backend"], "sqlite")
            store = create_conversation_store(config)
            self.assertIsInstance(store, ConversationStore)
            conversation = store.create_conversation(owner_id="factory-user")
            self.assertEqual(conversation["owner_id"], "factory-user")

    def test_remote_postgres_requires_tls(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("workspace: " + directory + "\n", encoding="utf-8")
            env = {
                "YUKI_STORAGE_BACKEND": "postgres",
                "YUKI_DATABASE_URL": "postgresql://yuki:secret@db.example.test/yuki",
            }
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(RuntimeError, "remote databases require TLS"):
                    load_config(config_path)

    def test_remote_postgres_accepts_verify_full(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("workspace: " + directory + "\n", encoding="utf-8")
            env = {
                "YUKI_STORAGE_BACKEND": "postgres",
                "YUKI_DATABASE_URL": "postgresql://yuki:secret@db.example.test/yuki?sslmode=verify-full",
            }
            with patch.dict(os.environ, env, clear=False):
                config = load_config(config_path)
            self.assertEqual(config["storage_backend"], "postgres")
            self.assertEqual(config["database_pool_min"], 1)
            self.assertEqual(config["database_pool_max"], 10)

    def test_pool_bounds_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("database_pool_min: 12\ndatabase_pool_max: 4\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Expected an integer"):
                load_config(config_path)

    def test_postgres_sql_translates_transaction_and_placeholders(self):
        self.assertEqual(
            PostgresConversationStore._postgres_sql("BEGIN IMMEDIATE"),
            "BEGIN",
        )
        self.assertEqual(
            PostgresConversationStore._postgres_sql("SELECT * FROM items WHERE id=? AND owner=?"),
            "SELECT * FROM items WHERE id=%s AND owner=%s",
        )



    @unittest.skipUnless(os.environ.get("YUKI_TEST_POSTGRES_URL"), "PostgreSQL integration URL not configured")
    def test_postgres_store_conversation_generation_and_lease_contract(self):
        store = PostgresConversationStore(
            os.environ["YUKI_TEST_POSTGRES_URL"], min_pool_size=1, max_pool_size=2
        )
        conversation_id = None
        owner_id = "postgres-integration-" + uuid.uuid4().hex
        try:
            conversation = store.create_conversation(owner_id=owner_id)
            conversation_id = conversation["conversation_id"]
            user = store.append_message(
                owner_id=owner_id, conversation_id=conversation_id, role="user",
                content="PostgreSQL integration", idempotency_key="request-1",
                expected_version=0, generation_request_id="request-1",
            )
            self.assertEqual(user["version"], 1)
            self.assertEqual(len(store.list_interrupted_generations(
                owner_id=owner_id, conversation_id=conversation_id
            )), 1)
            lease = store.acquire_generation_lease(
                owner_id=owner_id, conversation_id=conversation_id,
                lease_token="lease-" + uuid.uuid4().hex,
            )
            self.assertEqual(lease["conversation_id"], conversation_id)
            self.assertTrue(store.release_generation_lease(
                owner_id=owner_id, conversation_id=conversation_id,
                lease_token=next(iter([
                    row["lease_token"] for row in self._read_lease_tokens(store, conversation_id)
                ])),
            ))
            self.assertTrue(store.finish_generation(
                owner_id=owner_id, conversation_id=conversation_id,
                request_id="request-1", status="cancelled",
            ))
        finally:
            if conversation_id:
                with store._connect() as db:
                    db.execute("DELETE FROM conversations WHERE conversation_id=%s", (conversation_id,))
            store.close()

    @staticmethod
    def _read_lease_tokens(store, conversation_id):
        with store._connect() as db:
            return db.execute(
                "SELECT lease_token FROM conversation_generation_leases WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchall()

if __name__ == "__main__":
    unittest.main()
