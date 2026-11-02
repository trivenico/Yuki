import os
import tempfile
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


if __name__ == "__main__":
    unittest.main()
