import unittest

from core.side_effect_inventory import SIDE_EFFECT_SURFACES, COMMAND_DISPATCH_PATTERNS, production_inventory_report
import ast
from pathlib import Path


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

    def test_every_main_cli_dispatch_pattern_is_in_inventory(self):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        main_function = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
        )
        discovered = set()
        for node in ast.walk(main_function):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "command":
                for comparator in node.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        discovered.add(comparator.value)
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "command" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            ):
                discovered.add(node.args[0].value)
        self.assertEqual(
            discovered, set(COMMAND_DISPATCH_PATTERNS),
            "CLI dispatch changed. Update the command inventory and review authorization impact.",
        )

    def test_unverified_side_effect_surfaces_keep_production_blocked(self):
        report = production_inventory_report()
        self.assertFalse(report["production_ready"])
        self.assertGreater(len(report["outstanding"]), 0)


if __name__ == "__main__":
    unittest.main()
