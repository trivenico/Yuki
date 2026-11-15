import unittest

from core.component_catalog import DEFAULT_COMPONENTS
from core.network_firewall import NetworkFirewall
from core.network_firewall_component_runtime import build_network_firewall_component_runtime


class NetworkFirewallComponentRuntimeTests(unittest.TestCase):
    def test_runtime_lifecycle_binds_firewall_and_reports_health(self):
        firewall = NetworkFirewall(allowed_hosts=("api.example.com",))
        runtime = build_network_firewall_component_runtime(firewall)
        self.assertIs(runtime.instance, firewall)
        self.assertFalse(runtime.health_check())
        runtime.start()
        self.assertTrue(runtime.health_check())
        runtime.close()
        self.assertFalse(runtime.health_check())

    def test_runtime_rejects_wrong_contract(self):
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access")
        with self.assertRaises(ValueError):
            build_network_firewall_component_runtime(
                NetworkFirewall(allowed_hosts=("api.example.com",)), contract=contract,
            )

    def test_runtime_rejects_non_firewall_instance(self):
        with self.assertRaises(TypeError):
            build_network_firewall_component_runtime(object())


if __name__ == "__main__":
    unittest.main()
