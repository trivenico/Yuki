import unittest

from core.postgres_conversation_store import PostgresConversationStore


class PostgresSQLTranslationTests(unittest.TestCase):
    def test_boolean_archive_predicates_use_postgres_literals(self):
        self.assertEqual(
            PostgresConversationStore._postgres_sql(
                "UPDATE conversations SET archived=1 WHERE conversation_id=?"
            ),
            "UPDATE conversations SET archived=TRUE WHERE conversation_id=%s",
        )

    def test_archive_query_is_translated(self):
        self.assertEqual(
            PostgresConversationStore._postgres_sql(
                "SELECT * FROM conversations WHERE archived=0"
            ),
            "SELECT * FROM conversations WHERE archived=FALSE",
        )


if __name__ == "__main__":
    unittest.main()
