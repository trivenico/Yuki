"""Cross-component first-run test: CLI bootstrap through desktop durable chat."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.auth import BuiltInAuthService
from core.conversation_store import ConversationStore
from ui.desktop.auth import DesktopSessionManager
from ui.desktop.shared_conversation_session import build_authenticated_desktop_conversation
from yuki_cli import run_cli


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class FakeProvider:
    def generate_chat(self, *, messages, tools, tool_choice):
        return {"text": "First-run conversation persisted", "tool_calls": {"tool_calls": []}}


class DesktopFirstRunIntegrationTests(unittest.TestCase):
    def test_cli_bootstrap_desktop_login_and_conversation_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / ".yuki"
            workspace = Path(directory) / "workspace"
            passwords = iter([
                "A strong first-run password 7!",
                "A strong first-run password 7!",
            ])
            output = []

            with patch("yuki_cli.desktop_data_root", return_value=profile):
                code = run_cli(
                    ["auth", "bootstrap-admin"],
                    input_fn=lambda _prompt: "owner",
                    password_input=lambda _prompt: next(passwords),
                    output_fn=output.append,
                )

            self.assertEqual(code, 0, output)
            self.assertTrue((profile / "auth.sqlite3").is_file())
            self.assertIn("Launch Yuki Desktop", output[-1])
            self.assertNotIn("auth login", output[-1])

            auth = BuiltInAuthService(str(profile / "auth.sqlite3"))
            keyring = FakeKeyring()
            sessions = DesktopSessionManager(auth, keyring)
            identity = sessions.login("owner", "A strong first-run password 7!")
            self.assertEqual(sessions.current_identity(), identity)

            config = {"workspace": str(workspace)}
            provider = FakeProvider()
            store_factory = lambda cfg: ConversationStore(
                Path(cfg["workspace"]) / "conversations.sqlite3"
            )
            first = build_authenticated_desktop_conversation(
                config,
                provider=provider,
                identity=identity,
                sessions=sessions,
                store_factory=store_factory,
            )
            try:
                result = first.conversation.send(
                    "Hello from first run", request_id="first-run-request"
                )
                self.assertEqual(result["answer"], "First-run conversation persisted")
                conversation_id = result["conversation_id"]
            finally:
                first.close()

            reopened_identity = sessions.current_identity()
            self.assertEqual(reopened_identity, identity)
            reopened = build_authenticated_desktop_conversation(
                config,
                provider=provider,
                identity=reopened_identity,
                sessions=sessions,
                store_factory=store_factory,
            )
            try:
                history = reopened.conversation.history(conversation_id=conversation_id)
                self.assertEqual(
                    [item["content"] for item in history],
                    ["Hello from first run", "First-run conversation persisted"],
                )
            finally:
                reopened.close()
                sessions.logout()


if __name__ == "__main__":
    unittest.main()
