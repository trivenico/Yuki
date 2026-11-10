import unittest
from unittest.mock import patch

import yuki
from yuki_security import SecurityPolicy


class FakeAuth:
    def __init__(self, user):
        self.user = user

    def resolve_session(self, token):
        return self.user if token == "valid-session" else None


class User:
    user_id = "owner-1"


class TerminalToolDispatchTests(unittest.TestCase):
    def setUp(self):
        self.previous_token = yuki._CLI_SESSION_TOKEN
        self.calls = []
        self.tools = {"echo": lambda argument: self.calls.append(argument) or argument}
        self.policy = SecurityPolicy(allowed_tools=self.tools.keys())
        self.memory = []
        self.tasks = []
        self.agent = None
        self.providers = None

    def tearDown(self):
        yuki._CLI_SESSION_TOKEN = self.previous_token

    def test_missing_session_cannot_dispatch_tool(self):
        yuki._CLI_SESSION_TOKEN = None
        with patch.object(yuki, "_get_auth_service", return_value=FakeAuth(User())), patch.object(yuki, "TOOLS", self.tools):
            result = yuki.handle_command(
                "/tool echo do-not-run", self.memory, self.tasks,
                self.agent, self.providers, self.policy,
            )
        self.assertIn("Authentication required", result)
        self.assertEqual(self.calls, [])

    def test_expired_session_cannot_dispatch_tool(self):
        yuki._CLI_SESSION_TOKEN = "expired-session"
        with patch.object(yuki, "_get_auth_service", return_value=FakeAuth(None)), patch.object(yuki, "TOOLS", self.tools):
            result = yuki.handle_command(
                "/tool echo do-not-run", self.memory, self.tasks,
                self.agent, self.providers, self.policy,
            )
        self.assertIn("Authentication required", result)
        self.assertEqual(self.calls, [])

    def test_authenticated_tool_dispatch_passes_central_policy(self):
        yuki._CLI_SESSION_TOKEN = "valid-session"
        with patch.object(yuki, "_get_auth_service", return_value=FakeAuth(User())), patch.object(yuki, "TOOLS", self.tools):
            result = yuki.handle_command(
                "/tool echo hello", self.memory, self.tasks,
                self.agent, self.providers, self.policy,
            )
        self.assertEqual(result, "hello")
        self.assertEqual(self.calls, ["hello"])


if __name__ == "__main__":
    unittest.main()
