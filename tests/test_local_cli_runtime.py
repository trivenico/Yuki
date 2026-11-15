import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.local_cli_runtime import LocalConversationRuntime, build_local_conversation_runtime
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
    def test_concurrent_close_waits_for_cli_component_shutdown(self):
        entered = threading.Event()
        release = threading.Event()
        second_attempting = threading.Event()
        second_finished = threading.Event()

        class BlockingRegistry:
            def __init__(self):
                self.calls = 0

            def close(self):
                self.calls += 1
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test shutdown release timed out")
                return ()

        registry = BlockingRegistry()
        runtime = LocalConversationRuntime(object(), registry)
        first = threading.Thread(target=runtime.close)
        second = threading.Thread(
            target=lambda: (second_attempting.set(), runtime.close(), second_finished.set())
        )
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        self.assertTrue(second_attempting.wait(2))
        self.assertFalse(second_finished.wait(0.05))
        release.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertTrue(second_finished.is_set())
        self.assertEqual(registry.calls, 1)

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
                self.assertTrue(runtime.application.closed)

            reopened = ConversationStore(store_path)
            try:
                persisted = reopened.list_conversations(owner_id="local-test-owner-identity-000000000000")
                self.assertEqual(len(persisted), 1)
                self.assertEqual(persisted[0]["version"], 2)
            finally:
                pass

    def test_provider_startup_failure_closes_provider_and_application_store_once(self):
        class UnavailableProvider(FakeProvider):
            def is_available(self):
                return False

        with tempfile.TemporaryDirectory() as directory:
            provider = UnavailableProvider()
            store = ConversationStore(Path(directory) / "conversations.sqlite3")
            store.close = Mock()
            with self.assertRaisesRegex(RuntimeError, "component startup failed: provider-runtime"):
                build_local_conversation_runtime(
                    {"max_agent_steps": 3},
                    provider_factory=lambda config: provider,
                    store_factory=lambda config: store,
                    owner_id_factory=lambda: "local-test-owner-identity-000000000000",
                )
            self.assertEqual(provider.close_calls, 1)
            store.close.assert_called_once_with()

    def test_complete_configuration_is_bound_into_component_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            runtime = build_local_conversation_runtime(
                {
                    "max_agent_steps": 3,
                    "workspace": directory,
                    "provider": "mock",
                    "storage_backend": "sqlite",
                },
                provider_factory=lambda config: provider,
                store_factory=lambda config: ConversationStore(Path(directory) / "conversations.sqlite3"),
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
            try:
                self.assertIn("configuration-runtime", runtime.component_registry.started_components)
                snapshot = runtime.component_registry.resolve("configuration-runtime")
                self.assertEqual(snapshot.get("workspace"), directory)
                self.assertTrue(runtime.component_registry.health()["configuration-runtime"])
            finally:
                runtime.close()

    def test_runtime_readiness_reflects_started_components_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            runtime = build_local_conversation_runtime(
                {"max_agent_steps": 3},
                provider_factory=lambda config: provider,
                store_factory=lambda config: ConversationStore(Path(directory) / "conversations.sqlite3"),
                owner_id_factory=lambda: "local-test-owner-identity-000000000000",
            )
            self.assertEqual(runtime.readiness_report().capabilities["component_runtime"].status, "verified")
            runtime.close()
            self.assertEqual(runtime.readiness_report().capabilities["component_runtime"].status, "unavailable")
            self.assertEqual(provider.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
