"""Regression tests for canonical conversation database initialization."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore


class ConversationStoreSchemaTests(unittest.TestCase):
    def test_schema_initialization_rolls_back_partial_ddl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversation.sqlite3"
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"
            )
            db.commit()
            db.close()

            with self.assertRaises(sqlite3.OperationalError):
                ConversationStore(path)

            db = sqlite3.connect(path)
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            db.close()
            self.assertNotIn("conversation_messages", tables)
            self.assertNotIn("conversation_generation_leases", tables)
            self.assertNotIn("conversation_generation_runs", tables)


if __name__ == "__main__":
    unittest.main()
