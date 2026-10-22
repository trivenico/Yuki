import tempfile
import unittest
from pathlib import Path
from tools.repo_audit import audit, discover_python_files

class RepositoryAuditTests(unittest.TestCase):
    def test_detects_syntax_errors_and_skips_virtualenv(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"app.py").write_text("def broken(:\n",encoding="utf-8")
            (root/".venv").mkdir(); (root/".venv"/"ignored.py").write_text("bad syntax !!!",encoding="utf-8")
            report=audit(root)
            self.assertEqual(report["python_files_scanned"],1)
            self.assertEqual(report["syntax_error_count"],1)
            self.assertEqual(report["findings"][0]["rule"],"python-syntax-error")
    def test_detects_high_risk_patterns_without_importing_code(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/"app.py").write_text("import subprocess\nsubprocess.run('echo hi', shell=True)\neval('1+1')\n",encoding="utf-8")
            rules={item["rule"] for item in audit(root)["findings"]}
            self.assertIn("subprocess-shell-true",rules); self.assertIn("dynamic-code-execution",rules)
    def test_empty_repository_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            report=audit(Path(temp)); self.assertEqual(report["python_files_scanned"],0); self.assertEqual(report["syntax_error_count"],0)

if __name__=="__main__": unittest.main()
