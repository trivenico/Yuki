"""Static regression tests for explicit confirmation on high-risk CLI actions."""
import ast
import unittest
from pathlib import Path


class HighRiskCLIConfirmationTests(unittest.TestCase):
    def test_high_risk_handlers_confirm_before_side_effect(self):
        path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
        )
        expected = {
            "/security recon ": "scan_common_tcp",
            "/sandbox run ": "sandbox.run",
            "/defense block ": "block_temporary",
            "/defense remove ": "firewall.remove",
        }
        found = {}
        for node in ast.walk(main):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            literal = None
            if (
                isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
                and test.func.attr == "startswith" and isinstance(test.func.value, ast.Name)
                and test.func.value.id == "command" and test.args
                and isinstance(test.args[0], ast.Constant) and isinstance(test.args[0].value, str)
            ):
                literal = test.args[0].value
            elif isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "command":
                literal = next(
                    (item.value for item in test.comparators
                     if isinstance(item, ast.Constant) and isinstance(item.value, str)),
                    None,
                )
            if literal in expected:
                found[literal] = node.body

        self.assertEqual(set(found), set(expected))
        for command, body in found.items():
            calls = [
                item for statement in body for item in ast.walk(statement)
                if isinstance(item, ast.Call)
            ]
            confirm_lines = [
                item.lineno for item in calls
                if isinstance(item.func, ast.Name) and item.func.id == "_confirm"
            ]
            target = expected[command].split(".")[-1]
            effect_lines = [
                item.lineno for item in calls
                if isinstance(item.func, ast.Attribute) and item.func.attr == target
            ]
            self.assertTrue(confirm_lines, f"{command} lacks an explicit confirmation gate")
            self.assertTrue(effect_lines, f"{command} side-effect call was not found")
            self.assertLess(
                min(confirm_lines), min(effect_lines),
                f"{command} must confirm before invoking {target}",
            )


if __name__ == "__main__":
    unittest.main()
