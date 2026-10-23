import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki
from core.auth import BuiltInAuthService


class YukiCliEntrypointAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
            max_failed_logins=5,
            lockout_seconds=30,
        )
        self.auth.bootstrap_admin("root.user", "A secure local password 7!")

    def test_failed_login_never_constructs_command_runtime(self):
        answers = iter(["root.user"] * 3)
        passwords = iter(["wrong password 123"] * 3)
        with patch("yuki.ProviderManager") as providers, patch("builtins.print"):
            yuki.main(auth_service=self.auth, input_fn=lambda _: next(answers),
                      password_fn=lambda _: next(passwords))
        providers.assert_not_called()

    def test_authenticated_session_is_revoked_when_cli_exits(self):
        answers = iter(["root.user", "/quit"])
        passwords = iter(["A secure local password 7!"])
        with patch("yuki.load_memory", return_value=[]), patch("yuki.load_tasks", return_value=[]), patch("builtins.print"):
            yuki.main(auth_service=self.auth, input_fn=lambda _: next(answers),
                      password_fn=lambda _: next(passwords))
        # The only session created by this test was revoked by main's finally block.
        with self.auth._connect() as db:
            row = db.execute("SELECT revoked_at FROM auth_sessions ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["revoked_at"])

    def test_session_is_revoked_when_command_loop_raises(self):
        answers = iter(["root.user", "trigger", "/quit"])
        passwords = iter(["A secure local password 7!"])
        with patch("yuki.load_memory", return_value=[]), patch("yuki.load_tasks", return_value=[]), \
             patch("yuki.handle_command", side_effect=[RuntimeError("test failure"), "__QUIT__"]), patch("builtins.print"):
            yuki.main(auth_service=self.auth, input_fn=lambda _: next(answers),
                      password_fn=lambda _: next(passwords))
        with self.auth._connect() as db:
            row = db.execute("SELECT revoked_at FROM auth_sessions ORDER BY created_at DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row["revoked_at"])


if __name__ == "__main__":
    unittest.main()
