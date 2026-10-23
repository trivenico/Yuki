import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.auth import BuiltInAuthService
from core.cli_auth import authenticate_local_cli


class LocalCliAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
            max_failed_logins=5,
            lockout_seconds=30,
        )

    def test_first_run_bootstraps_admin_and_returns_session_without_printing_token(self):
        answers = iter(["root.user", "A secure local password 7!", "A secure local password 7!", "root.user"])
        passwords = iter(["A secure local password 7!", "A secure local password 7!", "A secure local password 7!"])
        with patch("builtins.print") as output:
            session = authenticate_local_cli(self.auth, input_fn=lambda _: next(answers), password_fn=lambda _: next(passwords))
        self.assertIsNotNone(session)
        self.assertEqual(session.user.role, "admin")
        self.assertNotIn(session.token, " ".join(str(call) for call in output.call_args_list))

    def test_password_mismatch_cancels_bootstrap(self):
        answers = iter(["root.user"])
        passwords = iter(["A secure local password 7!", "A different password 8!"])
        result = authenticate_local_cli(self.auth, input_fn=lambda _: next(answers), password_fn=lambda _: next(passwords))
        self.assertIsNone(result)
        self.assertEqual(self.auth.list_users(), [])

    def test_existing_account_login_returns_session(self):
        self.auth.bootstrap_admin("root.user", "A secure local password 7!")
        values = iter(["root.user"])
        passwords = iter(["A secure local password 7!"])
        result = authenticate_local_cli(self.auth, input_fn=lambda _: next(values), password_fn=lambda _: next(passwords))
        self.assertIsNotNone(result)
        self.assertEqual(result.user.username, "root.user")

    def test_invalid_credentials_are_bounded(self):
        self.auth.bootstrap_admin("root.user", "A secure local password 7!")
        values = iter(["root.user"] * 3)
        passwords = iter(["wrong password 123"] * 3)
        result = authenticate_local_cli(
            self.auth, input_fn=lambda _: next(values),
            password_fn=lambda _: next(passwords), max_attempts=3,
        )
        self.assertIsNone(result)

    def test_attempt_count_is_validated(self):
        with self.assertRaises(ValueError):
            authenticate_local_cli(self.auth, max_attempts=0)


if __name__ == "__main__":
    unittest.main()
