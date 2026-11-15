import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.auth import BuiltInAuthService
from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry
from core.identity_component_runtime import build_identity_access_component_runtime


class IdentityAccessComponentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
        )

    def test_registry_resolves_auth_service_and_probes_real_database(self):
        runtime = build_identity_access_component_runtime(self.auth)
        registry = ComponentRegistry()
        registry.register(runtime)

        self.assertIs(registry.resolve("identity-access"), self.auth)
        self.assertEqual(registry.health(), {})
        self.assertEqual(registry.start_all(), ("identity-access",))
        self.assertEqual(registry.health(), {"identity-access": True})
        self.assertEqual(registry.close(), ())
        self.assertFalse(runtime.health_check())

    def test_unavailable_database_fails_startup(self):
        runtime = build_identity_access_component_runtime(self.auth)
        registry = ComponentRegistry()
        registry.register(runtime)
        with patch.object(self.auth, "health_check", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "component startup failed: identity-access") as raised:
                registry.start_all()
            self.assertIn("identity/access storage", str(raised.exception.__cause__))
        self.assertEqual(registry.started_components, ())

    def test_health_probe_requires_strict_boolean(self):
        with patch.object(self.auth, "health_check", return_value=1):
            runtime = build_identity_access_component_runtime(self.auth)
            with self.assertRaisesRegex(TypeError, "must return bool"):
                runtime.start()

    def test_runtime_rejects_non_auth_service(self):
        with self.assertRaisesRegex(TypeError, "BuiltInAuthService"):
            build_identity_access_component_runtime(object())

    def test_wrong_contract_is_rejected(self):
        provider_contract = next(
            item for item in DEFAULT_COMPONENTS
            if item.component_id == "provider-runtime"
        )
        with self.assertRaisesRegex(ValueError, "identity-access"):
            build_identity_access_component_runtime(
                self.auth, contract=provider_contract
            )

    def test_closed_runtime_cannot_restart(self):
        runtime = build_identity_access_component_runtime(self.auth)
        runtime.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            runtime.start()


if __name__ == "__main__":
    unittest.main()
