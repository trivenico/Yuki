import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import load_config


class RemotePostgresTLSConfigurationTests(unittest.TestCase):
    def _load(self, database_url):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("workspace: " + directory + "\n", encoding="utf-8")
            env = {"YUKI_STORAGE_BACKEND": "postgres", "YUKI_DATABASE_URL": database_url}
            with patch.dict(os.environ, env, clear=True):
                return load_config(config_path)

    def test_remote_postgres_requires_hostname_verifying_tls(self):
        for mode in ("require", "verify-ca", "disable", ""):
            with self.subTest(mode=mode):
                suffix = "" if mode == "" else "?sslmode=" + mode
                with self.assertRaisesRegex(RuntimeError, "exactly one sslmode=verify-full"):
                    self._load("postgresql://yuki:secret@db.example.test/yuki" + suffix)

    def test_remote_postgres_rejects_ambiguous_duplicate_sslmode_parameters(self):
        for query in (
            "sslmode=verify-full&sslmode=disable",
            "sslmode=verify-full&sslmode=verify-full",
        ):
            with self.subTest(query=query):
                with self.assertRaisesRegex(RuntimeError, "exactly one sslmode=verify-full"):
                    self._load("postgresql://yuki:secret@db.example.test/yuki?" + query)

    def test_remote_postgres_accepts_exact_verify_full_parameter(self):
        config = self._load(
            "postgresql://yuki:secret@db.example.test/yuki?sslmode=verify-full"
        )
        self.assertEqual(config["storage_backend"], "postgres")

    def test_local_postgres_does_not_require_tls(self):
        config = self._load("postgresql://yuki:secret@127.0.0.1/yuki")
        self.assertEqual(config["storage_backend"], "postgres")


if __name__ == "__main__":
    unittest.main()
