import tempfile
import unittest
from pathlib import Path

from tools.dependency_audit import audit_files, normalize_name


class DependencyAuditTests(unittest.TestCase):
    def test_normalizes_pep503_name_separators(self):
        self.assertEqual(normalize_name("My_Package.Name"), "my-package-name")

    def test_detects_duplicate_normalized_names_in_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_text("Example_Pkg>=1\nexample-pkg<3\n", encoding="utf-8")
            errors = audit_files([path])
        self.assertTrue(any("duplicate package 'example-pkg'" in error for error in errors))

    def test_detects_conflicting_constraints_across_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "requirements.txt"
            second = Path(directory) / "requirements-optional.txt"
            first.write_text("demo>=1\n", encoding="utf-8")
            second.write_text("demo<2\n", encoding="utf-8")
            errors = audit_files([first, second])
        self.assertTrue(any("conflicting constraints for 'demo'" in error for error in errors))

    def test_allows_same_constraint_across_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "requirements.txt"
            second = Path(directory) / "requirements-optional.txt"
            first.write_text("demo>=1,<2\n", encoding="utf-8")
            second.write_text("demo>=1,<2\n", encoding="utf-8")
            self.assertEqual(audit_files([first, second]), [])

    def test_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_text("# comment\n\nPyYAML>=6 # parser\n", encoding="utf-8")
            self.assertEqual(audit_files([path]), [])


if __name__ == "__main__":
    unittest.main()
