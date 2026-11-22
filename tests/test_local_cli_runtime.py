import os
import tempfile
import unittest
from pathlib import Path

from core.agent_execution_loop import AgentExecutionError
from core.local_cli_runtime import build_local_conversation_runtime
from core.local_owner_identity import load_or_create_local_owner_id
from core.conversation_store import ConversationStore


class FakeProvider:
    def __init__(self):
        self.calls = []

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls.append((messages, tools, tool_choice))
        return {"text": "Persistent answer", "tool_calls": {"tool_calls": []}}


class LocalOwnerIdentityTests(unittest.TestCase):
    def test_identity_is_stable_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            first = load_or_create_local_owner_id(directory)
            second = load_or_create_local_owner_id(directory)
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("local-"))
            if os.name != "nt":
                mode = (Path(directory) / "local-owner-id").stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)

    def test_invalid_existing_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "local-owner-id").write_text("not-an-owner\\n", encoding="ascii")
            identity_path = Path(directory) / "local-owner-id"
            if os.name != "nt":
                identity_path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                load_or_create_local_owner_id(directory)


class LocalCliRuntimeTests(unittest.TestCase):
    def test_invalid_agent_step_limit_is_rejected_before_provider_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            initialized = []
            with self.assertRaisesRegex(ValueError, "max_agent_steps"):
                build_local_conversation_runtime(
                    {"max_agent_steps": True, "workspace": directory},
                    provider_factory=lambda config: initialized.append(True),
                    owner_id_factory=lambda: "local-test-owner-identity-000000000000",
                )
            self.assertEqual(initialized, [])

    def test_invalid_owner_identity_is_rejected_before_provider_initialization(self):
        initialized = []
        with self.assertRaisesRegex(ValueError, "owner identity"):
            build_local_conversation_runtime(
                {},
                provider_factory=lambda config: initialized.append(True),
                owner_id_factory=lambda: " ",
            )
        self.assertEqual(initialized, [])

    def test_runtime_uses_guarded_agent_loop_and_persists_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = FakeProvider()
            store_path = root / "conversations.sqlite3"
            runtime = build_local_conversation_runtime(
                {"max_agent_steps": 3},
                provider_factory=lambda config: provider,
                store_factory=lambda config: ConversationStore(store_path),
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
            try:
                self.assertEqual(provider.calls, [])
                conversation = runtime.conversations.create(owner_id=runtime.owner_id)
                first = runtime.conversations.reply(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                    user_text="Remember this conversation",
                    request_id="request-one",
                    expected_version=conversation["version"],
                    tools=[],
                )
                self.assertEqual(first["answer"], "Persistent answer")
                self.assertEqual(first["version"], 2)
                self.assertEqual(len(provider.calls), 1)
                history = runtime.conversations.history(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                )
                self.assertEqual([item["role"] for item in history], ["user", "assistant"])
                self.assertEqual(provider.calls[0][1], [])
            finally:
                runtime.close()

            reopened = ConversationStore(store_path)
            try:
                persisted = reopened.list_conversations(owner_id="local-test-owner-identity-000000000000")
                self.assertEqual(len(persisted), 1)
                self.assertEqual(persisted[0]["version"], 2)
            finally:
                reopened.close()

    def test_provider_initialization_failure_does_not_block_local_conversation_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            initialized = []

            def unavailable_provider(config):
                initialized.append(True)
                raise RuntimeError("sensitive credential or endpoint detail")

            runtime = build_local_conversation_runtime(
                {},
                provider_factory=unavailable_provider,
                store_factory=lambda config: ConversationStore(Path(directory) / "chat.sqlite3"),
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
            try:
                self.assertEqual(initialized, [])
                conversation = runtime.conversations.create(owner_id=runtime.owner_id)
                self.assertEqual(len(runtime.conversations.list(owner_id=runtime.owner_id)), 1)
                with self.assertRaises(AgentExecutionError):
                    runtime.conversations.agent_loop.run(
                        "A model-dependent request", tools=[], system_text="You are Yuki."
                    )
                self.assertEqual(initialized, [True])
                with self.assertRaises(AgentExecutionError):
                    runtime.conversations.agent_loop.run(
                        "A second model-dependent request", tools=[], system_text="You are Yuki."
                    )
                self.assertEqual(initialized, [True])
                self.assertEqual(
                    runtime.conversations.list(owner_id=runtime.owner_id)[0]["conversation_id"],
                    conversation["conversation_id"],
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
