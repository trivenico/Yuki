import unittest
from unittest.mock import Mock

from core.component_registry import ComponentRegistry
from core.provider_component_runtime import build_provider_component_runtime


class ProviderComponentRuntimeTests(unittest.TestCase):
    def test_binds_concrete_provider_and_probes_readiness_on_start(self):
        provider = Mock()
        provider.is_available.return_value = True
        runtime = build_provider_component_runtime(provider)

        self.assertIs(runtime.instance, provider)
        self.assertEqual(provider.is_available.call_count, 0)
        runtime.start()
        self.assertEqual(provider.is_available.call_count, 1)
        self.assertTrue(runtime.health_check())
        self.assertEqual(provider.is_available.call_count, 2)

    def test_start_fails_closed_when_provider_is_unavailable(self):
        provider = Mock()
        provider.is_available.return_value = False
        runtime = build_provider_component_runtime(provider)

        with self.assertRaisesRegex(RuntimeError, "provider is unavailable"):
            runtime.start()
        self.assertFalse(runtime.health_check())

    def test_start_rejects_non_boolean_provider_readiness(self):
        provider = Mock()
        provider.is_available.return_value = 1
        runtime = build_provider_component_runtime(provider)

        with self.assertRaisesRegex(TypeError, "must return bool"):
            runtime.start()

    def test_close_invokes_optional_provider_cleanup_exactly_once(self):
        provider = Mock()
        provider.is_available.return_value = True
        runtime = build_provider_component_runtime(provider)
        runtime.start()

        runtime.close()
        runtime.close()

        provider.close.assert_called_once_with()
        self.assertFalse(runtime.health_check())

    def test_close_is_idempotent_even_when_provider_cleanup_fails(self):
        provider = Mock()
        provider.is_available.return_value = True
        provider.close.side_effect = OSError("provider cleanup failed")
        runtime = build_provider_component_runtime(provider)
        runtime.start()

        with self.assertRaisesRegex(OSError, "provider cleanup failed"):
            runtime.close()
        runtime.close()
        provider.close.assert_called_once_with()

    def test_component_registry_uses_provider_lifecycle_adapter(self):
        provider = Mock()
        provider.is_available.return_value = True
        registry = ComponentRegistry()
        registry.register(build_provider_component_runtime(provider))

        self.assertEqual(registry.start_all(), ("provider-runtime",))
        self.assertEqual(registry.health(), {"provider-runtime": True})
        self.assertEqual(registry.close(), ())


if __name__ == "__main__":
    unittest.main()
