"""Tests for the desktop composition of the canonical conversation runtime."""
import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore
from ui.desktop.runtime import build_desktop_conversation_runtime


class _Provider:
    def __init__(self):
        self.calls = []
        self.closed = 0

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls.append((messages, tools, tool_choice))
        return {"text": "Desktop runtime reply", "tool_calls": {"tool_calls": []}}

    def close(self):
        self.closed += 1


class DesktopRuntimeCompositionTests(unittest.TestCase):
    def test_uses_authenticated_owner_and_exposes_no_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = _Provider()
            runtime = build_desktop_conversation_runtime(
                {"workspace": directory},
                provider=provider,
                owner_id="authenticated-user-id",
                store_factory=lambda config: ConversationStore(
                    Path(config["workspace"]) / "conversations.sqlite3"
                ),
            )
            try:
                conversation = runtime.conversations.create(
                    owner_id=runtime.owner_id, title="Desktop",
                )
                result = runtime.conversations.reply(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                    user_text="Hello",
                    request_id="desktop-runtime-request",
                    expected_version=0,
                    tools=[],
                )
                self.assertEqual(result["answer"], "Desktop runtime reply")
                self.assertEqual(provider.calls[0][1], [])
                self.assertEqual(provider.calls[0][2], "auto")
                self.assertEqual(
                    runtime.conversations.list(owner_id="another-user"), [],
                )
            finally:
                runtime.close()
                runtime.close()
            self.assertEqual(provider.closed, 1)

    def test_rejects_invalid_owner_before_opening_store(self):
        opened = []
        with self.assertRaisesRegex(ValueError, "owner_id"):
            build_desktop_conversation_runtime(
                {}, provider=_Provider(), owner_id=" ",
                store_factory=lambda config: opened.append(True),
            )
        self.assertEqual(opened, [])

    def test_provider_without_streaming_uses_non_streaming_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = _Provider()
            runtime = build_desktop_conversation_runtime(
                {"workspace": directory}, provider=provider,
                owner_id="desktop-owner",
                store_factory=lambda config: ConversationStore(
                    Path(config["workspace"]) / "conversations.sqlite3"
                ),
            )
            try:
                conversation = runtime.conversations.create(owner_id="desktop-owner")
                result = runtime.conversations.reply(
                    owner_id="desktop-owner",
                    conversation_id=conversation["conversation_id"],
                    user_text="No streaming",
                    request_id="non-streaming",
                    expected_version=0,
                    tools=[],
                )
                self.assertEqual(result["status"], "completed")
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
