import unittest

from core.side_effect_inventory import SIDE_EFFECT_SURFACES, production_inventory_report


class SideEffectInventoryTests(unittest.TestCase):
    def test_inventory_is_non_empty_and_commands_are_unique(self):
        self.assertTrue(SIDE_EFFECT_SURFACES)
        commands = [item.command for item in SIDE_EFFECT_SURFACES]
        self.assertEqual(len(commands), len(set(commands)))

    def test_only_explicitly_verified_surfaces_count_as_ready(self):
        report = production_inventory_report()
        self.assertEqual(report["surface_count"], len(SIDE_EFFECT_SURFACES))
        self.assertEqual(report["ready_count"], sum(
            item.durable_controls_integrated and item.integration_tested
            for item in SIDE_EFFECT_SURFACES
        ))
        self.assertFalse(report["production_ready"])
        self.assertIn("/defense sniff|block|remove", report["outstanding"])

    def test_unverified_surface_cannot_be_reported_ready(self):
        for item in SIDE_EFFECT_SURFACES:
            if not item.durable_controls_integrated or not item.integration_tested:
                self.assertIn(item.command, production_inventory_report()["outstanding"])


if __name__ == "__main__":
    unittest.main()
