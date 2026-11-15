import os
import tempfile
import unittest
from pathlib import Path

from core.local_cli_runtime import build_local_conversation_runtime
from core.local_owner_identity import load_or_create_local_owner_id
from core.conversation_store import ConversationStore


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.close_calls = 0

    def close(self):
        self.close_calls += 1

    def generate(self, prompt):
        return prompt

    def is_available(self):
        return True

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
            (Path(directory) / "local-owner-id").write_text("not-an-owner\n", encoding="ascii")
            identity_path = Path(directory) / "local-owner-id"
            if os.name != "nt":
                identity_path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                load_or_create_local_owner_id(directory)


class LocalCliRuntimeTests(unittest.TestCase):
    def test_provider_is_closed_if_cli_composition_fails_before_ownership_transfer(self):
        provider = FakeProvider()
        with self.assertRaisesRegex(ValueError, "max_agent_steps"):
            build_local_conversation_runtime(
                {"max_agent_steps": 0}, provider_factory=lambda config: provider,
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
        self.assertEqual(provider.close_calls, 1)

    def test_provider_is_closed_if_store_construction_fails(self):
        provider = FakeProvider()
        with self.assertRaisesRegex(RuntimeError, "store unavailable"):
            build_local_conversation_runtime(
                {"max_agent_steps": 3}, provider_factory=lambda config: provider,
                store_factory=lambda config: (_ for _ in ()).throw(RuntimeError("store unavailable")),
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
        self.assertEqual(provider.close_calls, 1)

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
                self.assertEqual(set(runtime.component_registry.started_components), {
                    "identity-access", "provider-runtime", "agent-execution",
                    "durable-conversations", "application-composition",
                })
                self.assertTrue(all(runtime.component_registry.health().values()))
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
                history = runtime.conversations.history(
                    owner_id=runtime.owner_id,
                    conversation_id=conversation["conversation_id"],
                )
                self.assertEqual([item["role"] for item in history], ["user", "assistant"])
                self.assertEqual(provider.calls[0][1], [])
            finally:
                runtime.close()
                self.assertEqual(provider.close_calls, 1)
                self.assertTrue(runtime.application._closed)

            reopened = ConversationStore(store_path)
            try:
                persisted = reopened.list_conversations(owner_id="local-test-owner-identity-000000000000")
                self.assertEqual(len(persisted), 1)
                self.assertEqual(persisted[0]["version"], 2)
            finally:
                pass


if __name__ == "__main__":
    unittest.main()
