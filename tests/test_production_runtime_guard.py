import unittest

from core.production_guard import assert_production_runtime_supported


class ProductionRuntimeGuardTests(unittest.TestCase):
    def test_development_runtime_is_allowed(self):
        assert_production_runtime_supported({"environment": "development"})

    def test_test_runtime_is_allowed(self):
        assert_production_runtime_supported({"environment": "test"})

    def test_staging_runtime_is_allowed(self):
        assert_production_runtime_supported({"environment": "staging"})

    def test_production_runtime_fails_closed_until_execution_wiring_is_complete(self):
        with self.assertRaisesRegex(RuntimeError, "durable approval, audit, and execution-ledger"):
            assert_production_runtime_supported({"environment": "production"})

    def test_missing_environment_defaults_to_development(self):
        assert_production_runtime_supported({})


if __name__ == "__main__":
    unittest.main()
