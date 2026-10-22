"""Regression tests for durable authorization wiring of high-risk CLI commands."""
import ast
import unittest
from pathlib import Path


class DurableOperationWiringTests(unittest.TestCase):
    def test_high_risk_handlers_use_durable_operation_runtime(self):
        path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
        )
        expected = {
            "/security recon ": "recon_scan",
            "/sandbox run ": "sandbox_run",
            "/defense block ": "firewall_block",
            "/defense remove ": "firewall_remove",
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
            if literal in expected:
                found[literal] = node.body

        self.assertEqual(set(found), set(expected))
        for command, body in found.items():
            execute_calls = [
                node for statement in body for node in ast.walk(statement)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "durable_operations"
            ]
            self.assertTrue(execute_calls, f"{command} bypasses DurableOperationRuntime")
            self.assertTrue(
                any(
                    any(isinstance(arg, ast.Constant) and arg.value == expected[command] for arg in call.args)
                    for call in execute_calls
                ),
                f"{command} uses the wrong registered operation",
            )

    def test_operation_runtime_uses_allowlisted_handlers_only(self):
        source = (Path(__file__).resolve().parents[1] / "core/durable_operation_runtime.py").read_text()
        tree = ast.parse(source)
        runtime = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DurableOperationRuntime")
        methods = {node.name: node for node in runtime.body if isinstance(node, ast.FunctionDef)}
        executor = ast.unparse(methods["execute_approved_action"])
        self.assertIn("self.operations[operation]", executor)
        self.assertNotIn("importlib", executor)
        self.assertNotIn("eval(", executor)


if __name__ == "__main__":
    unittest.main()
