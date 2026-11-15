import unittest

from core.component_registry import ComponentRegistry
from providers.component_runtime import build_provider_component_runtime


class FakeProvider:
    def __init__(self, available=True):
        self.available = available
        self.closed = 0

    def generate(self, prompt):
        return prompt

    def is_available(self):
        return self.available

    def close(self):
        self.closed += 1


class ProviderComponentRuntimeTests(unittest.TestCase):
    def test_registry_owns_real_provider_and_uses_its_availability_probe(self):
        provider = FakeProvider()
        registry = ComponentRegistry()
        runtime = build_provider_component_runtime(provider)
        registry.register(runtime)

        self.assertIs(registry.resolve("provider-runtime"), provider)
        self.assertEqual(registry.health(), {})
        self.assertEqual(registry.start_all(), ("provider-runtime",))
        self.assertEqual(registry.health(), {"provider-runtime": True})
        self.assertEqual(registry.close(), ())
        self.assertEqual(provider.closed, 1)

    def test_unavailable_provider_fails_startup_and_is_cleaned_up(self):
        provider = FakeProvider(available=False)
        registry = ComponentRegistry()
        registry.register(build_provider_component_runtime(provider))

        with self.assertRaisesRegex(RuntimeError, "provider-runtime"):
            registry.start_all()
        self.assertEqual(provider.closed, 1)
        self.assertEqual(registry.started_components, ())

    def test_provider_health_must_return_a_boolean(self):
        class InvalidProvider(FakeProvider):
            def is_available(self):
                return 1

        registry = ComponentRegistry()
        registry.register(build_provider_component_runtime(InvalidProvider()))
        with self.assertRaisesRegex(TypeError, "must return bool"):
            registry.start_all()

    def test_provider_without_close_hook_is_supported(self):
        class StatelessProvider:
            def generate(self, prompt):
                return prompt

            def is_available(self):
                return True

        registry = ComponentRegistry()
        provider = StatelessProvider()
        registry.register(build_provider_component_runtime(provider))
        registry.start_all()
        self.assertEqual(registry.close(), ())

    def test_invalid_provider_contract_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "generate"):
            build_provider_component_runtime(object())

    def test_close_failure_is_reported_by_registry(self):
        class BrokenProvider(FakeProvider):
            def close(self):
                raise OSError("cleanup failed")

        registry = ComponentRegistry()
        registry.register(build_provider_component_runtime(BrokenProvider()))
        registry.start_all()
        self.assertEqual(registry.close(), ("provider-runtime",))


if __name__ == "__main__":
    unittest.main()
