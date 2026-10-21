"""Regression tests for fail-closed production startup ordering."""
import ast
import unittest
from pathlib import Path


class ProductionStartupOrderingTests(unittest.TestCase):
    def test_guard_runs_before_workspace_recovery(self):
        source_path = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        main_function = next(
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "main"
        )

        def first_statement_index(function, call_name):
            for index, statement in enumerate(function.body):
                if any(
                    isinstance(node, ast.Call)
                    and (
                        (isinstance(node.func, ast.Name) and node.func.id == call_name)
                        or (isinstance(node.func, ast.Attribute) and node.func.attr == call_name)
                    )
                    for node in ast.walk(statement)
                ):
                    return index
            return None

        guard_index = first_statement_index(
            main_function, "assert_production_runtime_supported"
        )
        recovery_index = first_statement_index(
            main_function, "recover_workspace_on_startup"
        )
        self.assertIsNotNone(guard_index, "main() must enforce the production runtime guard")
        self.assertIsNotNone(recovery_index, "main() must keep startup recovery explicit")
        self.assertLess(
            guard_index,
            recovery_index,
            "Production guard must run before workspace recovery",
        )


if __name__ == "__main__":
    unittest.main()
