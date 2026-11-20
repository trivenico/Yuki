import unittest

from automation.workspace_change_impact import WorkspaceChangeImpact
from automation.regression_test_planner import prepare_regression_test_plan


def impact(**overrides):
    values = dict(
        changed_paths=("src/core.py",),
        changed_python_modules=("src.core",),
        direct_importers=("tests/test_core.py", "src/service.py"),
        transitive_importers=("tests/test_api.py",),
        test_candidates=("tests/test_core.py", "tests/test_api.py"),
        configuration_paths=(),
        dependency_paths=(),
        security_sensitive_paths=(),
        parse_errors=(),
        analyzed_python_files=12,
    )
    values.update(overrides)
    return WorkspaceChangeImpact(**values)


class RegressionTestPlannerTests(unittest.TestCase):
    def test_prioritizes_direct_tests_before_transitive_tests(self):
        plan = prepare_regression_test_plan(
            impact(),
            ("tests/test_api.py", "tests/test_core.py", "tests/test_unrelated.py"),
        )
        self.assertEqual(
            [item.path for item in plan.candidates],
            ["tests/test_core.py", "tests/test_api.py"],
        )
        self.assertEqual(plan.candidates[0].priority, "high")
        self.assertEqual(plan.candidates[1].priority, "normal")
        self.assertFalse(plan.full_suite_recommended)
        self.assertFalse(plan.tests_executed)

    def test_recommends_full_suite_for_sensitive_changes(self):
        plan = prepare_regression_test_plan(
            impact(security_sensitive_paths=("automation/security_policy.py",)),
            ("tests/test_security.py",),
        )
        self.assertTrue(plan.full_suite_recommended)
        self.assertIn(
            "security-sensitive paths warrant broad regression coverage",
            plan.full_suite_reasons,
        )

    def test_recommends_full_suite_when_analysis_is_incomplete(self):
        plan = prepare_regression_test_plan(
            impact(parse_errors=("automation/broken.py",)),
            ("tests/test_core.py",),
        )
        self.assertTrue(plan.full_suite_recommended)

    def test_recommends_full_suite_when_no_targeted_tests_exist(self):
        plan = prepare_regression_test_plan(impact(), ("tests/test_other.py",))
        self.assertTrue(plan.full_suite_recommended)
        self.assertEqual(plan.candidates, ())

    def test_rejects_duplicate_test_paths(self):
        with self.assertRaises(ValueError):
            prepare_regression_test_plan(impact(), ("tests/test_core.py", "tests/test_core.py"))

    def test_rejects_paths_outside_tests_directory_and_traversal(self):
        for path in ("automation/secret.py", "../tests/test_core.py", "tests/../x.py", "tests\\\\test_core.py", "/tests/test_core.py"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    prepare_regression_test_plan(impact(), (path,))

    def test_rejects_malformed_impact_path_metadata(self):
        malformed = impact(test_candidates=("../escape.py",))
        with self.assertRaises(ValueError):
            prepare_regression_test_plan(malformed, ("tests/test_core.py",))

    def test_rejects_mutable_path_collection(self):
        with self.assertRaises(TypeError):
            prepare_regression_test_plan(impact(), ["tests/test_core.py"])


if __name__ == "__main__":
    unittest.main()
