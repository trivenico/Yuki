import unittest

from core.postgres_database_identity import postgres_database_identity


class PostgresDatabaseIdentityTests(unittest.TestCase):
    def test_credentials_do_not_change_endpoint_identity(self):
        first = postgres_database_identity(
            "postgresql://workflow_role:secret-one@db.internal:5432/yuki?sslmode=require"
        )
        second = postgres_database_identity(
            "postgresql://lease_role:secret-two@db.internal:5432/yuki?sslmode=verify-full"
        )
        self.assertEqual(first, second)
        self.assertNotIn("secret", first)
        self.assertNotIn("workflow_role", first)

    def test_database_name_changes_endpoint_identity(self):
        first = postgres_database_identity("postgresql://role:secret@db.internal:5432/yuki")
        second = postgres_database_identity("postgresql://role:secret@db.internal:5432/yuki_test")
        self.assertNotEqual(first, second)

    def test_host_changes_endpoint_identity(self):
        first = postgres_database_identity("postgresql://role:secret@db.internal:5432/yuki")
        second = postgres_database_identity("postgresql://role:secret@db-secondary.internal:5432/yuki")
        self.assertNotEqual(first, second)

    def test_invalid_or_ambiguous_endpoint_is_rejected(self):
        for value in ("", "not-a-postgres-url", "postgresql://role:secret/db"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    postgres_database_identity(value)


if __name__ == "__main__":
    unittest.main()
