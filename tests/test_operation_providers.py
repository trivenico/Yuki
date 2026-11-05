import unittest

from automation.operation_providers import (
    OperationProviderRegistry,
    ProviderCapabilities,
    ProviderCapabilityError,
)


class FakeProvider:
    def execute(self, action, request, *, idempotency_key):
        return {"status": "accepted"}

    def lookup(self, *, idempotency_key):
        return None


class OperationProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = OperationProviderRegistry()
        self.provider = FakeProvider()

    def test_register_and_require_declared_capabilities(self):
        registered = self.registry.register(
            "local.mock", self.provider,
            ProviderCapabilities(
                supports_idempotency_key=True,
                supports_operation_lookup=True,
            ),
        )
        self.assertIs(self.registry.require_idempotent_dispatch("local.mock"), registered)
        self.assertIs(self.registry.require_operation_lookup("local.mock"), registered)
        self.assertEqual(self.registry.names(), ("local.mock",))

    def test_dispatch_rejected_without_idempotency_guarantee(self):
        self.registry.register(
            "legacy.mock", self.provider,
            ProviderCapabilities(False, True),
        )
        with self.assertRaises(ProviderCapabilityError):
            self.registry.require_idempotent_dispatch("legacy.mock")

    def test_lookup_rejected_when_provider_cannot_reconcile(self):
        self.registry.register(
            "writeonly.mock", self.provider,
            ProviderCapabilities(True, False),
        )
        with self.assertRaises(ProviderCapabilityError):
            self.registry.require_operation_lookup("writeonly.mock")

    def test_duplicate_registration_never_overwrites_provider(self):
        first = self.registry.register(
            "local.mock", self.provider, ProviderCapabilities(True, True),
        )
        with self.assertRaises(ProviderCapabilityError):
            self.registry.register(
                "local.mock", FakeProvider(), ProviderCapabilities(False, False),
            )
        self.assertIs(self.registry.get("local.mock"), first)

    def test_invalid_names_and_capability_types_are_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.register("UpperCase", self.provider, ProviderCapabilities(True, True))
        with self.assertRaises(ValueError):
            ProviderCapabilities(1, True)

    def test_provider_must_implement_both_contract_methods(self):
        class Incomplete:
            def execute(self, action, request, *, idempotency_key):
                return {}

        with self.assertRaises(TypeError):
            self.registry.register(
                "incomplete.mock", Incomplete(), ProviderCapabilities(True, True),
            )


if __name__ == "__main__":
    unittest.main()
