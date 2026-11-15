import unittest

from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry
from core.configuration_component_runtime import (
    ConfigurationSnapshot,
    build_configuration_component_runtime,
)


class ConfigurationComponentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "workspace": "/tmp/yuki-workspace",
            "provider": "ollama",
            "storage_backend": "sqlite",
            "mcp_servers": [{"name": "docs", "allowed_tools": ["search"]}],
            "database_url": "postgresql://user:secret@example.invalid/db",
        }

    def test_registers_immutable_snapshot_and_reports_lifecycle_health(self):
        runtime = build_configuration_component_runtime(self.config)
        self.assertIsInstance(runtime.instance, ConfigurationSnapshot)
        self.assertEqual(runtime.instance.get("provider"), "ollama")
        registry = ComponentRegistry()
        registry.register(runtime)
        self.assertEqual(registry.start_all(), ("configuration-runtime",))
        self.assertEqual(registry.health(), {"configuration-runtime": True})
        self.assertEqual(registry.close(), ())
        with self.assertRaisesRegex(RuntimeError, "registry is closed"):
            registry.health()

    def test_snapshot_is_deeply_immutable_and_detached_from_source(self):
        runtime = build_configuration_component_runtime(self.config)
        self.config["provider"] = "mutated"
        self.config["mcp_servers"][0]["allowed_tools"].append("delete")
        snapshot = runtime.instance
        self.assertEqual(snapshot.get("provider"), "ollama")
        self.assertEqual(snapshot.get("mcp_servers")[0]["allowed_tools"], ("search",))
        self.assertNotIn("secret", repr(snapshot))
        with self.assertRaises(TypeError):
            snapshot.values["provider"] = "mutated"
        with self.assertRaises(TypeError):
            snapshot.get("mcp_servers")[0]["name"] = "changed"

    def test_invalid_configuration_is_rejected_before_registration(self):
        with self.assertRaisesRegex(ValueError, "workspace"):
            build_configuration_component_runtime({"provider": "ollama"})
        with self.assertRaisesRegex(ValueError, "storage_backend"):
            build_configuration_component_runtime({
                "workspace": "/tmp/yuki", "provider": "ollama",
                "storage_backend": "unknown",
            })

    def test_non_mapping_configuration_is_rejected(self):
        with self.assertRaisesRegex(TypeError, "mapping"):
            build_configuration_component_runtime(None)

    def test_opaque_mutable_values_and_non_string_keys_are_rejected(self):
        with self.assertRaisesRegex(TypeError, "scalar primitives"):
            build_configuration_component_runtime({
                "workspace": "/tmp/yuki",
                "provider": "ollama",
                "opaque": bytearray(b"mutable"),
            })
        with self.assertRaisesRegex(TypeError, "keys must be strings"):
            build_configuration_component_runtime({
                "workspace": "/tmp/yuki",
                "provider": "ollama",
                "nested": {1: "invalid key"},
            })

    def test_wrong_component_contract_is_rejected(self):
        wrong_contract = next(
            item for item in DEFAULT_COMPONENTS
            if item.component_id == "provider-runtime"
        )
        with self.assertRaisesRegex(ValueError, "configuration-runtime"):
            build_configuration_component_runtime(self.config, contract=wrong_contract)

    def test_health_does_not_expose_secret_values(self):
        runtime = build_configuration_component_runtime(self.config)
        registry = ComponentRegistry()
        registry.register(runtime)
        registry.start_all()
        health = registry.health()
        self.assertEqual(health, {"configuration-runtime": True})
        self.assertNotIn("secret", repr(health))
        registry.close()


if __name__ == "__main__":
    unittest.main()
