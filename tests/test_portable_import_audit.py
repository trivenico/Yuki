import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.portable_import_audit import audit_imports, main


class PortableImportAuditTests(unittest.TestCase):
    def test_audit_classifies_stdlib_local_and_external_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "core").mkdir()
            (root / "core" / "agent.py").write_text(
                "import json\nfrom pathlib import Path\nimport yaml\n"
                "from providers.factory import create\n",
                encoding="utf-8",
            )
            (root / "providers").mkdir()
            (root / "providers" / "factory.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "main.py").write_text("import core.agent\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "ignored.py").write_text("import unittest\n", encoding="utf-8")

            report = audit_imports(root)

            self.assertIn("json", report["stdlib"])
            self.assertIn("pathlib", report["stdlib"])
            self.assertIn("core", report["local"])
            self.assertIn("providers", report["local"])
            self.assertIn("yaml", report["third_party"])
            self.assertNotIn("unittest", report["third_party"])

    def test_audit_reports_parse_errors_with_relative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "broken.py"):
                audit_imports(root)

    def test_cli_emits_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("import json\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["--root", str(root), "--json"])
            self.assertEqual(result, 0)
            payload = json.loads(output.getvalue())
            self.assertIn("json", payload["stdlib"])


if __name__ == "__main__":
    unittest.main()
