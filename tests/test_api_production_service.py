import unittest

from api.production_service import load_production_app
from api.runtime import ConversationRuntime
from core.production_security_runtime import ProductionSecurityRuntime


class FakeApplication:
    store = object()

    def close(self):
        pass


class APIProductionServiceTests(unittest.TestCase):
    def test_factory_must_be_callable(self):
        with self.assertRaisesRegex(TypeError, "api_bootstrap_factory_required"):
            load_production_app(None)

    def test_factory_must_return_conversation_runtime(self):
        with self.assertRaisesRegex(TypeError, "api_bootstrap_must_return_conversation_runtime"):
            load_production_app(lambda: object())

    def test_security_runtime_must_be_production_type(self):
        runtime = ConversationRuntime.__new__(ConversationRuntime)
        runtime.production_security_runtime = object()
        runtime.production_security = object()
        with self.assertRaisesRegex(TypeError, "api_runtime_must_own_production_security_runtime"):
            load_production_app(lambda: runtime)

    def test_context_must_be_the_runtime_owned_context(self):
        security_runtime = object.__new__(ProductionSecurityRuntime)
        security_runtime.security = object()
        security_runtime.verify_readiness = lambda: None
        app = ConversationRuntime(
            object(), FakeApplication(),
            production_security=object(),
            production_security_runtime=security_runtime,
        )
        app.production_security = object()
        with self.assertRaisesRegex(ValueError, "api_runtime_security_context_mismatch"):
            load_production_app(lambda: app)

    def test_valid_runtime_is_readiness_checked_before_return(self):
        calls = []
        security_runtime = object.__new__(ProductionSecurityRuntime)
        security_runtime.security = object()
        security_runtime.verify_readiness = lambda: calls.append("ready")
        app = ConversationRuntime(
            object(), FakeApplication(),
            production_security=security_runtime.security,
            production_security_runtime=security_runtime,
        )
        self.assertIs(load_production_app(lambda: app), app)
        self.assertEqual(calls, ["ready"])


if __name__ == "__main__":
    unittest.main()
