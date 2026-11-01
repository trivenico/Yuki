from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from tools.defensive_code_audit import scan_python

class DefensiveCodeAuditTests(unittest.TestCase):
    def scan(self, source):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text(source, encoding="utf-8")
            findings, errors = scan_python(root)
        self.assertEqual(errors, [])
        return {f.rule_id: f for f in findings}

    def test_dynamic_execution_and_shell_true(self):
        found = self.scan("import subprocess\ndef run(user_input):\n eval(user_input)\n subprocess.run(user_input, shell=True)\n")
        self.assertIn("YSEC001", found)
        self.assertIn("YSEC008", found)

    def test_pickle_and_unsafe_yaml(self):
        found = self.scan("import pickle, yaml\npickle.loads(data)\nyaml.load(data)\nyaml.safe_load(data)\n")
        self.assertIn("YSEC003", found)
        self.assertIn("YSEC006", found)
        self.assertEqual(len(found), 2)

    def test_disabled_tls_and_temp_path(self):
        found = self.scan("import requests, tempfile\nrequests.get(url, verify=False)\ntempfile.mktemp()\n")
        self.assertIn("YSEC007", found)
        self.assertIn("YSEC004", found)

    def test_credential_literal_and_placeholder(self):
        found = self.scan('api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"\npassword = "change_me"\n')
        self.assertIn("YSEC009", found)
        self.assertEqual(len(found), 1)

    def test_weak_hash(self):
        self.assertIn("YSEC005", self.scan("import hashlib\nhashlib.md5(payload)\n"))

    def test_skips_virtualenv_and_reports_parse_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".venv").mkdir()
            (root / ".venv" / "bad.py").write_text("eval('x')", encoding="utf-8")
            (root / "broken.py").write_text("def broken(:", encoding="utf-8")
            findings, errors = scan_python(root)
        self.assertEqual(findings, [])
        self.assertEqual(errors[0]["path"], "broken.py")

if __name__ == "__main__":
    unittest.main()
