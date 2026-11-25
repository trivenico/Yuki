import unittest
from unittest.mock import patch

from api.production_service import load_production_app


class FakeSecurity:
    def verify_readiness(self):
        return None


class FakeSecurityRuntime:
    def __init__(self, security):
        self.security = security

    def verify_readiness(self):
        return None


class FakeConversationRuntime:
    def __init__(self, security_runtime):
        self.production_security_runtime = security_runtime
        self.production_security = security_runtime.security


class APIProductionServiceTests(unittest.TestCase):
    def test_factory_must_be_callable(self):
        with self.assertRaisesRegex(TypeError, "api_bootstrap_factory_required"):
            load_production_app(None)

    def test_factory_must_return_conversation_runtime(self):
        with self.assertRaisesRegex(TypeError, "api_bootstrap_must_return_conversation_runtime"):
            load_production_app(lambda: object())

    def test_security_runtime_must_be_production_type(self):
        from api.runtime import ConversationRuntime
        # A real ConversationRuntime is required; malformed values are rejected
        # before the launcher can serve traffic.
        runtime = ConversationRuntime.__new__(ConversationRuntime)
        runtime.production_security_runtime = object()
        runtime.production_security = object()
        with self.assertRaisesRegex(TypeError, "api_runtime_must_own_production_security_runtime"):
            load_production_app(lambda: runtime)


if __name__ == "__main__":
    unittest.main()
