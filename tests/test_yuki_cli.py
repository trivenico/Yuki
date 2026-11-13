import json
import tempfile
import unittest
from pathlib import Path

from yuki_cli import run_cli, _resolve_cli_provider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.profile_store import ProviderProfileStore


class FakeProvider:
    def __init__(self, available=True, answer="hello"):
        self.available = available
        self.answer = answer
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.answer


class FakeReport:
    def to_dict(self):
        return {"operating_system": "TestOS", "capabilities": {}}


class YukiCliTests(unittest.TestCase):
    def test_provider_profile_cli_lifecycle_uses_hidden_secret_input(self):
        class MemoryVault:
            def __init__(self):
                self.values = {}
            def set_secret(self, reference, secret):
                self.values[reference] = secret
            def get_secret(self, reference):
                return self.values.get(reference)
            def delete_secret(self, reference):
                self.values.pop(reference, None)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profiles.sqlite"
            vault = MemoryVault()
            def store_factory(config, *, create):
                if not create and not database.exists():
                    return None
                return ProviderProfileStore(database, vault)
            owner_factory = lambda config: "owner:cli"
            outputs = []
            code = run_cli(
                ["provider-profile", "add", "--name", "gateway",
                 "--endpoint", "https://gateway.example.test/v1",
                 "--model", "vendor/model-x", "--approved-host", "gateway.example.test"],
                config_loader=lambda: {}, profile_store_factory=store_factory,
                owner_id_factory=owner_factory,
                secret_input_fn=lambda prompt: "never-print-this-secret",
                output_fn=outputs.append,
            )
            self.assertEqual(code, 0)
            self.assertNotIn("never-print-this-secret", repr(outputs))
            outputs.clear()
            code = run_cli(
                ["provider-profile", "list"], config_loader=lambda: {},
                profile_store_factory=store_factory, owner_id_factory=owner_factory,
                output_fn=outputs.append,
            )
            self.assertEqual(code, 0)
            listing = json.loads(outputs[0])
            self.assertEqual(listing["profiles"][0]["name"], "gateway")
            self.assertTrue(listing["profiles"][0]["credential_configured"])
            self.assertNotIn("never-print-this-secret", outputs[0])
            code = run_cli(
                ["provider-profile", "select", "gateway"], config_loader=lambda: {},
                profile_store_factory=store_factory, owner_id_factory=owner_factory,
                output_fn=outputs.append,
            )
            self.assertEqual(code, 0)
            code = run_cli(
                ["provider-profile", "verify", "gateway"], config_loader=lambda: {},
                profile_store_factory=store_factory, owner_id_factory=owner_factory,
                output_fn=outputs.append,
            )
            self.assertEqual(code, 0)
            verification = json.loads(outputs[-1])
            self.assertTrue(verification["local_ready"])
            self.assertFalse(verification["network_checked"])
            code = run_cli(
                ["provider-profile", "remove", "gateway"], config_loader=lambda: {},
                profile_store_factory=store_factory, owner_id_factory=owner_factory,
                output_fn=outputs.append,
            )
            self.assertEqual(code, 0)
            store = store_factory({}, create=False)
            self.assertIsNone(store.selected_profile("owner:cli"))
            store.close()

    def test_profile_select_fails_closed_without_vault_credential(self):
        class MemoryVault:
            def __init__(self):
                self.values = {}
            def set_secret(self, reference, secret):
                self.values[reference] = secret
            def get_secret(self, reference):
                return self.values.get(reference)
            def delete_secret(self, reference):
                self.values.pop(reference, None)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profiles.sqlite"
            vault = MemoryVault()
            store = ProviderProfileStore(database, vault)
            store.save(
                owner_id="owner:cli", name="gateway",
                base_url="https://gateway.example.test/v1", model="vendor/model-x",
                allowed_host="gateway.example.test", api_key="secret",
            )
            vault.values.clear()
            def store_factory(config, *, create):
                return ProviderProfileStore(database, vault)
            output = []
            code = run_cli(
                ["provider-profile", "select", "gateway"], config_loader=lambda: {},
                profile_store_factory=store_factory,
                owner_id_factory=lambda config: "owner:cli", output_fn=output.append,
            )
            self.assertEqual(code, 3)
            reopened = ProviderProfileStore(database, vault)
            self.assertIsNone(reopened.selected_profile("owner:cli"))
            reopened.close()
            store.close()

    def test_selected_saved_profile_resolves_for_runtime_without_plaintext_config(self):
        class MemoryVault:
            def __init__(self):
                self.values = {}
            def set_secret(self, reference, secret):
                self.values[reference] = secret
            def get_secret(self, reference):
                return self.values.get(reference)
            def delete_secret(self, reference):
                self.values.pop(reference, None)

        with tempfile.TemporaryDirectory() as directory:
            vault = MemoryVault()
            database = Path(directory) / "profiles.sqlite"
            store = ProviderProfileStore(database, vault)
            store.save(
                owner_id="owner:cli", name="gateway",
                base_url="https://gateway.example.test/v1", model="vendor/model-x",
                allowed_host="gateway.example.test", api_key="vault-secret",
            )
            store.select_profile("owner:cli", "gateway")
            def store_factory(config, *, create):
                if not create and not database.exists():
                    return None
                return ProviderProfileStore(database, vault)
            adapter, opened_store, owner, manager = _resolve_cli_provider(
                {}, lambda config: self.fail("selected profile must override default provider"),
                store_factory, lambda config: "owner:cli",
            )
            self.assertIsInstance(adapter, OpenAICompatibleProvider)
            self.assertTrue(adapter.is_available())
            self.assertEqual(owner, "owner:cli")
            self.assertEqual(manager.current().name, "gateway")
            self.assertNotIn("vault-secret", repr(manager.catalog()))
            opened_store.close()
            store.close()

    def test_chat_uses_persistent_runtime_and_closes_it(self):
        class Conversations:
            def __init__(self):
                self.items = []
                self.history_calls = 0
                self.reply_calls = 0

            def list(self, *, owner_id, limit):
                return list(self.items[:limit])

            def create(self, *, owner_id, title="New conversation"):
                item = {"conversation_id": "conversation-1234567890", "title": title, "version": 0}
                self.items.insert(0, item)
                return item

            def history(self, *, owner_id, conversation_id, limit=100):
                self.history_calls += 1
                return [{"role": "user", "content": "hello"}]

            def reply(self, **kwargs):
                self.reply_calls += 1
                return {"answer": "saved answer", "version": 2, "status": "completed"}

        class Runtime:
            def __init__(self):
                self.owner_id = "local-test-owner"
                self.conversations = Conversations()
                self.closed = False

            def close(self):
                self.closed = True

        runtime = Runtime()
        output = []
        inputs = iter(["hello", "/history", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            conversation_runtime_factory=lambda config: runtime,
            input_fn=lambda prompt: next(inputs),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(runtime.closed)
        self.assertEqual(runtime.conversations.reply_calls, 1)
        self.assertEqual(runtime.conversations.history_calls, 1)
        self.assertIn("Yuki> saved answer", output)

    def test_ask_uses_configured_provider_and_returns_answer(self):
        provider = FakeProvider(answer="real answer")
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {"provider": "fake"},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, ["real answer"])
        self.assertEqual(provider.prompts, ["hello"])

    def test_provider_status_reports_unavailable_with_nonzero_exit(self):
        provider = FakeProvider(available=False)
        output = []
        code = run_cli(
            ["provider-status"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "unavailable")

    def test_doctor_is_json_and_does_not_initialize_provider(self):
        output = []
        def forbidden_provider_factory(config):
            raise AssertionError("doctor must not initialize a provider")
        code = run_cli(
            ["doctor"],
            provider_factory=forbidden_provider_factory,
            readiness_builder=FakeReport,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[0])["operating_system"], "TestOS")

    def test_ask_returns_nonzero_when_provider_generation_fails(self):
        class FailingProvider(FakeProvider):
            def generate(self, prompt):
                raise RuntimeError("private payload")

        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FailingProvider(),
            output_fn=output.append,
        )
        self.assertEqual(code, 4)
        self.assertNotIn("private payload", output[0])

    def test_ask_returns_unavailable_exit_code(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FakeProvider(available=False),
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        self.assertIn("unavailable", output[0].lower())

    def test_provider_initialization_failure_is_sanitized(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: (_ for _ in ()).throw(RuntimeError("secret payload")),
            output_fn=output.append,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("secret payload", output[0])


if __name__ == "__main__":
    unittest.main()
