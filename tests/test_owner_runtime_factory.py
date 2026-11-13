import tempfile
import unittest
from pathlib import Path

from core.guarded_tool_runtime import GuardedToolRuntime
from core.agent_execution_loop import AgentExecutionLoop
from core.conversation_service import ConversationService
from core.conversation_store import ConversationStore
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter
from providers.owner_runtime_factory import OwnerAgentLoopFactory
from providers.openai_compatible import OpenAICompatibleProvider
from providers.profile_store import ProviderProfileStore


class MemoryVault:
    def __init__(self):
        self.values = {}

    def set_secret(self, reference, secret):
        self.values[reference] = secret

    def get_secret(self, reference):
        return self.values.get(reference)

    def delete_secret(self, reference):
        self.values.pop(reference, None)


class AllowNothing:
    def authorize_tool(self, name):
        return False


class OwnerAgentLoopFactoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.store = ProviderProfileStore(
            Path(self.directory.name) / "profiles.sqlite3", self.vault
        )
        for owner, profile, host, secret in (
            ("owner:alice", "alice_gateway", "alice.example.test", "alice-secret"),
            ("owner:bob", "bob_gateway", "bob.example.test", "bob-secret"),
        ):
            self.store.save(
                owner_id=owner, name=profile,
                base_url=f"https://{host}/v1", model=f"model/{owner.split(':')[1]}",
                allowed_host=host, api_key=secret,
            )
            self.store.select_profile(owner, profile)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_factory_resolves_distinct_owner_profiles_and_vault_credentials(self):
        factory = OwnerAgentLoopFactory(
            self.store,
            tool_runtime_for_owner=lambda owner: GuardedToolRuntime({}, AllowNothing()),
        )
        alice = factory("owner:alice")
        bob = factory("owner:bob")
        self.assertIsInstance(alice.model, ProviderAgentAdapter)
        self.assertIsInstance(bob.model, ProviderAgentAdapter)
        alice_provider = alice.model.provider
        bob_provider = bob.model.provider
        self.assertIsInstance(alice_provider, OpenAICompatibleProvider)
        self.assertIsInstance(bob_provider, OpenAICompatibleProvider)
        self.assertEqual(alice_provider.base_url, "https://alice.example.test/v1")
        self.assertEqual(bob_provider.base_url, "https://bob.example.test/v1")
        self.assertEqual(alice_provider._environ["YUKI_VAULT_PROFILE_API_KEY"], "alice-secret")
        self.assertEqual(bob_provider._environ["YUKI_VAULT_PROFILE_API_KEY"], "bob-secret")
        self.assertIsNot(alice_provider, bob_provider)
        self.assertIsNot(alice.tool_protocol, bob.tool_protocol)


    def test_concurrent_owner_resolution_keeps_adapters_and_credentials_isolated(self):
        import threading

        factory = OwnerAgentLoopFactory(self.store)
        barrier = threading.Barrier(2)
        results = {}
        errors = []

        def resolve(owner):
            try:
                barrier.wait(timeout=5)
                results[owner] = factory(owner)
            except Exception as exc:
                errors.append(exc)

        workers = [
            threading.Thread(target=resolve, args=("owner:alice",)),
            threading.Thread(target=resolve, args=("owner:bob",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        alice = results["owner:alice"].model.provider
        bob = results["owner:bob"].model.provider
        self.assertIsNot(alice, bob)
        self.assertEqual(alice._environ["YUKI_VAULT_PROFILE_API_KEY"], "alice-secret")
        self.assertEqual(bob._environ["YUKI_VAULT_PROFILE_API_KEY"], "bob-secret")
        self.assertNotEqual(alice.base_url, bob.base_url)

    def test_protocol_reads_immutable_public_registered_tool_catalog(self):
        runtime = GuardedToolRuntime({"owner.read": lambda argument: argument}, AllowNothing())
        self.assertEqual(runtime.registered_tool_names, frozenset({"owner.read"}))
        self.assertEqual(
            runtime.registered_tool_names,
            runtime.registered_tool_names,
        )
        with self.assertRaises(AttributeError):
            runtime.registered_tool_names.add("owner.write")

    def test_factory_fails_closed_without_owner_selected_profile(self):
        self.store.delete("owner:alice", "alice_gateway")
        factory = OwnerAgentLoopFactory(self.store)
        with self.assertRaisesRegex(RuntimeError, "no available selected provider"):
            factory("owner:alice")

    def test_factory_rejects_shared_guarded_runtime_between_owners(self):
        shared = GuardedToolRuntime({}, AllowNothing())
        factory = OwnerAgentLoopFactory(
            self.store, tool_runtime_for_owner=lambda owner: shared,
        )
        factory("owner:alice")
        with self.assertRaisesRegex(PermissionError, "cannot be shared across owners"):
            factory("owner:bob")

    def test_factory_rejects_shared_tool_handler_between_owners(self):
        shared_handler = lambda argument: argument
        factory = OwnerAgentLoopFactory(
            self.store,
            tool_runtime_for_owner=lambda owner: GuardedToolRuntime(
                {"owner.read": shared_handler}, AllowNothing(),
            ),
        )
        factory("owner:alice")
        with self.assertRaisesRegex(PermissionError, "tool handler cannot be shared"):
            factory("owner:bob")

    def test_factory_allows_distinct_owner_bound_tool_handlers(self):
        factory = OwnerAgentLoopFactory(
            self.store,
            tool_runtime_for_owner=lambda owner: GuardedToolRuntime(
                {"owner.read": lambda argument: owner + ":" + argument}, AllowNothing(),
            ),
        )
        alice = factory("owner:alice")
        bob = factory("owner:bob")
        self.assertIsNot(alice.tool_protocol, bob.tool_protocol)

    def test_factory_rejects_shared_authorization_policy_between_owners(self):
        shared_policy = AllowNothing()
        factory = OwnerAgentLoopFactory(
            self.store,
            tool_runtime_for_owner=lambda owner: GuardedToolRuntime({}, shared_policy),
        )
        factory("owner:alice")
        with self.assertRaisesRegex(PermissionError, "authorization policy cannot be shared"):
            factory("owner:bob")


    def test_factory_rejects_shared_audit_sink_between_owners(self):
        shared_audit = lambda name, outcome: None
        factory = OwnerAgentLoopFactory(
            self.store,
            tool_runtime_for_owner=lambda owner: GuardedToolRuntime(
                {}, AllowNothing(), audit=shared_audit,
            ),
        )
        factory("owner:alice")
        with self.assertRaisesRegex(PermissionError, "audit sink cannot be shared"):
            factory("owner:bob")


    def test_factory_rejects_shared_bound_audit_sink_between_owners(self):
        class AuditSink:
            def record(self, name, outcome):
                pass

        shared_sink = AuditSink()
        factory = OwnerAgentLoopFactory(
            self.store,
            tool_runtime_for_owner=lambda owner: GuardedToolRuntime(
                {}, AllowNothing(), audit=shared_sink.record,
            ),
        )
        factory("owner:alice")
        with self.assertRaisesRegex(PermissionError, "audit sink cannot be shared"):
            factory("owner:bob")

    def test_factory_observes_owner_profile_selection_changes_on_next_turn(self):
        self.store.save(
            owner_id="owner:alice", name="second_gateway",
            base_url="https://second.example.test/v1", model="model/second",
            allowed_host="second.example.test", api_key="second-secret",
        )
        factory = OwnerAgentLoopFactory(self.store)
        first = factory("owner:alice")
        self.store.select_profile("owner:alice", "second_gateway")
        second = factory("owner:alice")
        self.assertEqual(first.model.provider.base_url, "https://alice.example.test/v1")
        self.assertEqual(second.model.provider.base_url, "https://second.example.test/v1")
        self.assertIsNot(first.model.provider, second.model.provider)

    def test_factory_rejects_invalid_owner_identity(self):
        factory = OwnerAgentLoopFactory(self.store)
        for owner in ("", "  ", None, 7):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                factory(owner)


    def test_concurrent_owner_conversations_reach_only_their_selected_provider(self):
        import threading

        conversations = ConversationStore(
            Path(self.directory.name) / "conversations.sqlite3"
        )
        alice_conversation = conversations.create_conversation(owner_id="owner:alice")
        bob_conversation = conversations.create_conversation(owner_id="owner:bob")
        factory = OwnerAgentLoopFactory(self.store)
        barrier = threading.Barrier(2)
        observed = {}
        errors = []

        def owner_loop(owner_id):
            loop = factory(owner_id)
            provider = loop.model.provider

            def generate_chat(*, messages, tools, tool_choice):
                observed[owner_id] = {
                    "base_url": provider.base_url,
                    "model": provider.model,
                    "credential": provider._environ["YUKI_VAULT_PROFILE_API_KEY"],
                }
                barrier.wait(timeout=5)
                return {
                    "text": "response:" + provider.model,
                    "tool_calls": {"tool_calls": []},
                }

            # Replace only the network boundary: the real owner factory,
            # provider adapter, agent loop and conversation service still run.
            provider.generate_chat = generate_chat
            return loop

        fallback = AgentExecutionLoop(
            lambda **kwargs: {"text": "fallback", "tool_calls": {"tool_calls": []}},
            StructuredToolProtocol(GuardedToolRuntime({}, AllowNothing())),
        )
        service = ConversationService(
            conversations, fallback, agent_loop_for_owner=owner_loop
        )
        results = {}

        def reply(owner_id, conversation, request_id):
            try:
                results[owner_id] = service.reply(
                    owner_id=owner_id,
                    conversation_id=conversation["conversation_id"],
                    user_text="hello",
                    request_id=request_id,
                    expected_version=0,
                    tools=[],
                )
            except Exception as exc:
                errors.append(exc)

        workers = [
            threading.Thread(target=reply, args=("owner:alice", alice_conversation, "alice-request")),
            threading.Thread(target=reply, args=("owner:bob", bob_conversation, "bob-request")),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(results["owner:alice"]["answer"], "response:model/alice")
        self.assertEqual(results["owner:bob"]["answer"], "response:model/bob")
        self.assertEqual(observed["owner:alice"]["base_url"], "https://alice.example.test/v1")
        self.assertEqual(observed["owner:bob"]["base_url"], "https://bob.example.test/v1")
        self.assertEqual(observed["owner:alice"]["credential"], "alice-secret")
        self.assertEqual(observed["owner:bob"]["credential"], "bob-secret")
        self.assertNotIn("alice-secret", results["owner:alice"]["answer"])
        self.assertNotIn("bob-secret", results["owner:bob"]["answer"])

if __name__ == "__main__":
    unittest.main()
