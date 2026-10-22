"""Regression tests for durable authorization of persistent evolution operations."""
import ast
import unittest
from pathlib import Path


class DurableEvolutionOperationTests(unittest.TestCase):
    def setUp(self):
        self.source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.main = next(
            node for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
        )

    def test_evolution_mutations_are_registered(self):
        registered = set()
        for node in ast.walk(self.main):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "DurableOperationRuntime":
                continue
            for keyword in node.keywords:
                if keyword.arg == "operations" and isinstance(keyword.value, ast.Dict):
                    registered.update(
                        key.value for key in keyword.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )
        self.assertTrue(
            {"evolution_record", "evolution_run", "evolution_review"}.issubset(registered)
        )

    def test_evolution_handlers_use_durable_runtime(self):
        expected = {
            "/evolution record ": "evolution_record",
            "/evolution run": "evolution_run",
            "/evolution review ": "evolution_review",
        }
        found = {}
        for node in ast.walk(self.main):
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
                node for statement in body for node in ast.walk(statement)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "durable_operations"
            ]
            self.assertTrue(calls, f"{command} bypasses durable approval")
            self.assertTrue(
                any(
                    isinstance(arg, ast.Constant) and arg.value == expected[command]
                    for call in calls for arg in call.args
                ),
                f"{command} invokes the wrong registered operation",
            )


if __name__ == "__main__":
    unittest.main()
