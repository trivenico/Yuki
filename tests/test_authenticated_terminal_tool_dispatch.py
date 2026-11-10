"""Security regression tests for authenticated terminal tool dispatch."""

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yuki
from yuki_security import SecurityPolicy


class AuthenticatedTerminalToolTests(unittest.TestCase):
    def setUp(self):
        self.original_token = yuki._CLI_SESSION_TOKEN
        self.addCleanup(setattr, yuki, "_CLI_SESSION_TOKEN", self.original_token)

    def test_tool_command_rejects_missing_session_before_dispatch(self):
        calls = []
        yuki._CLI_SESSION_TOKEN = None
        auth = SimpleNamespace(resolve_session=lambda _token: None)
        with patch.object(yuki, "_get_auth_service", return_value=auth), \
             patch.object(yuki, "TOOLS", {"write": lambda argument: calls.append(argument) or "done"}):
            result = yuki.handle_command(
                "/tool write change", [], [], None, None,
                SecurityPolicy(allowed_tools={"write"}),
            )
        self.assertIn("Authentication required", result)
        self.assertEqual(calls, [])

    def test_tool_command_rejects_expired_session_before_dispatch(self):
        calls = []
        yuki._CLI_SESSION_TOKEN = "expired-session"
        auth = SimpleNamespace(resolve_session=lambda _token: None)
        with patch.object(yuki, "_get_auth_service", return_value=auth), \
             patch.object(yuki, "TOOLS", {"write": lambda argument: calls.append(argument) or "done"}):
            result = yuki.handle_command(
                "/tool write change", [], [], None, None,
                SecurityPolicy(allowed_tools={"write"}),
            )
        self.assertIn("Authentication required", result)
        self.assertEqual(calls, [])

    def test_authenticated_tool_dispatch_uses_central_and_local_policy(self):
        calls = []
        yuki._CLI_SESSION_TOKEN = "valid-session"
        auth = SimpleNamespace(
            resolve_session=lambda _token: SimpleNamespace(user_id="alice"),
        )
        with patch.object(yuki, "_get_auth_service", return_value=auth), \
             patch.object(yuki, "TOOLS", {"write": lambda argument: calls.append(argument) or "done"}):
            result = yuki.handle_command(
                "/tool write change", [], [], None, None,
                SecurityPolicy(allowed_tools={"write"}),
            )
        self.assertEqual(result, "done")
        self.assertEqual(calls, ["change"])

    def test_local_allowlist_remains_an_independent_gate(self):
        calls = []
        yuki._CLI_SESSION_TOKEN = "valid-session"
        auth = SimpleNamespace(
            resolve_session=lambda _token: SimpleNamespace(user_id="alice"),
        )
        with patch.object(yuki, "_get_auth_service", return_value=auth), \
             patch.object(yuki, "TOOLS", {"write": lambda argument: calls.append(argument) or "done"}):
            result = yuki.handle_command(
                "/tool write change", [], [], None, None,
                SecurityPolicy(allowed_tools=set()),
            )
        self.assertIn("blocked", result.lower())
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
