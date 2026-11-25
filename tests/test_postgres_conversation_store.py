from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.postgres_conversation_store import PostgresConversationStore


class PostgresConversationSchemaTests(unittest.TestCase):
    def _store_with_schema(self, missing=()):
        store = object.__new__(PostgresConversationStore)
        context = MagicMock()
        db = context.__enter__.return_value
        required = (
            "conversations",
            "conversation_messages",
            "conversation_generation_leases",
            "conversation_generation_runs",
        )
        db.execute.side_effect = [
            _Result(None if table in missing else f"public.{table}")
            for table in required
        ]
        store._connect = MagicMock(return_value=context)
        return store, db

    def test_schema_verification_is_read_only(self):
        store, db = self._store_with_schema()
        store._verify_schema()
        self.assertEqual(db.execute.call_count, 4)
        for call in db.execute.call_args_list:
            self.assertIn("to_regclass", call.args[0].lower())
            self.assertNotIn("CREATE", call.args[0].upper())
            self.assertNotIn("ALTER", call.args[0].upper())
            self.assertEqual(call.args[1][0].split(".", 1)[0], "public")

    def test_missing_migration_fails_with_operator_guidance(self):
        store, db = self._store_with_schema(missing={"conversation_messages"})
        with self.assertRaisesRegex(RuntimeError, "yuki-db-migrate --apply"):
            store._verify_schema()
        self.assertEqual(db.execute.call_count, 4)


class _Result:
    def __init__(self, relation):
        self._relation = relation

    def fetchone(self):
        return {"relation": self._relation}


if __name__ == "__main__":
    unittest.main()
