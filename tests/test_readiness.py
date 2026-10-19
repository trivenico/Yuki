import unittest

from core.readiness import CapabilityState, REQUIRED_RELEASE_GATES, build_readiness_report


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "provider": "mock",
            "plugin_discovery_enabled": False,
            "voice_enabled": False,
            "wakeword_enabled": False,
            "web_allowed_hosts": [],
        }

    def test_default_report_never_claims_production_ready(self):
        report = build_readiness_report(self.config)
        self.assertFalse(report.production_ready)
        self.assertTrue(report.blockers)
        self.assertEqual(len(report.release_gates), len(REQUIRED_RELEASE_GATES))

    def test_plugin_discovery_disabled_by_default_is_reported(self):
        report = build_readiness_report(self.config)
        capability = next(c for c in report.capabilities if c.name == "plugin_discovery")
        self.assertEqual(capability.state, CapabilityState.DISABLED)

    def test_configured_voice_is_not_assumed_healthy(self):
        report = build_readiness_report({**self.config, "voice_enabled": True})
        capability = next(c for c in report.capabilities if c.name == "voice")
        self.assertEqual(capability.state, CapabilityState.UNVERIFIED)

    def test_gate_requires_nonempty_evidence(self):
        gates = {name: " " for name in REQUIRED_RELEASE_GATES}
        report = build_readiness_report(self.config, verified_gates=gates)
        self.assertFalse(report.production_ready)
        self.assertTrue(any(not gate.passed for gate in report.release_gates))

    def test_runtime_failure_is_blocker(self):
        report = build_readiness_report(self.config, runtime_health={"provider_inference": False})
        self.assertFalse(report.production_ready)
        self.assertTrue(any("provider_inference" in blocker for blocker in report.blockers))

    def test_dict_is_json_friendly(self):
        result = build_readiness_report(self.config).to_dict()
        self.assertIsInstance(result["capabilities"], list)
        self.assertEqual(result["capabilities"][0]["state"], "needs_configuration")

    def test_non_mapping_config_rejected(self):
        with self.assertRaises(TypeError):
            build_readiness_report(None)


if __name__ == "__main__":
    unittest.main()
