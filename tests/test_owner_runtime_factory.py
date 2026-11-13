import tempfile
import unittest
from pathlib import Path

from core.guarded_tool_runtime import GuardedToolRuntime
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


if __name__ == "__main__":
    unittest.main()
