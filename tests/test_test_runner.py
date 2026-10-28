import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

from core.test_runner import run_project_unittest_suite


class ProjectTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()

    def _project(self, relative="demo"):
        project = self.workspace / relative
        (project / "tests").mkdir(parents=True)
        return project

    def test_records_real_success_and_nonzero_test_count(self):
        project = self._project()
        (project / "tests" / "test_demo.py").write_text(
            "import unittest\nclass Checks(unittest.TestCase):\n"
            " def test_true(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        result = run_project_unittest_suite(self.workspace, "demo")
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["passed"])
        self.assertEqual(result["test_count"], 1)
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["scope"], "python_unittest_discovery_only")

    def test_records_failure_without_claiming_success(self):
        project = self._project()
        (project / "tests" / "test_demo.py").write_text(
            "import unittest\nclass Checks(unittest.TestCase):\n"
            " def test_false(self): self.assertTrue(False)\n",
            encoding="utf-8",
        )
        result = run_project_unittest_suite(self.workspace, "demo")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertFalse(result["passed"])
        self.assertEqual(result["test_count"], 1)

    def test_rejects_path_traversal_and_absolute_project_paths(self):
        project = self._project()
        (project / "tests" / "test_demo.py").write_text(
            "import unittest\nclass Checks(unittest.TestCase):\n"
            " def test_true(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "inside"):
            run_project_unittest_suite(self.workspace, "../outside")
        with self.assertRaisesRegex(ValueError, "Absolute"):
            run_project_unittest_suite(self.workspace, str(project))

    def test_rejects_missing_or_empty_test_suite(self):
        project = self._project()
        with self.assertRaisesRegex(ValueError, "No top-level"):
            run_project_unittest_suite(self.workspace, "demo")

    def test_refuses_frozen_runtime_until_isolated_runner_is_packaged(self):
        with patch.object(sys, "frozen", True, create=True):
            with self.assertRaisesRegex(ValueError, "packaged desktop runtime"):
                run_project_unittest_suite(self.workspace, ".")

    def test_timeout_result_has_same_structured_fields_as_normal_run(self):
        project = self._project()
        (project / "tests" / "test_slow.py").write_text(
            "import time, unittest\\nclass Checks(unittest.TestCase):\\n"
            " def test_wait(self): time.sleep(3)\\n",
            encoding="utf-8",
        )
        result = run_project_unittest_suite(self.workspace, "demo", timeout_seconds=1)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["passed"])
        self.assertIsNone(result["exit_code"])
        self.assertEqual(result["test_count"], 0)
        self.assertIn("stdout", result)
        self.assertIn("stderr", result)

    def test_rejects_invalid_timeout(self):
        project = self._project()
        (project / "tests" / "test_demo.py").write_text(
            "import unittest\nclass Checks(unittest.TestCase):\n"
            " def test_true(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 120"):
            run_project_unittest_suite(self.workspace, "demo", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
