import unittest
from providers.factory import create_provider
from skills.device_scanner import DeviceScannerSkill


class StabilizationRegressionTests(unittest.TestCase):
    def test_unknown_provider_is_rejected_before_local_provider_construction(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "not-a-provider"}, environ={})

    def test_device_scanner_accepts_inventory_without_workspace_or_network_access(self):
        skill = DeviceScannerSkill()
        result = skill.execute('{"device":"test phone","updates_current":true,"screen_lock":true,"encryption":true,"unknown_apps":false}')
        self.assertTrue(result.ok)
        self.assertFalse(result.data["automated_vulnerability_scan"])


if __name__ == "__main__":
    unittest.main()
