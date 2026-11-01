from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from tools.secret_audit import scan

class SecretAuditTests(unittest.TestCase):
    def test_detects_and_redacts_token_like_value(self):
        token = "ghp_" + ("A" * 35)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.env").write_text("GITHUB_TOKEN=" + token + "\n", encoding="utf-8")
            findings, errors = scan(root)
        self.assertEqual(errors, [])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].redacted_match, "[REDACTED]")
        self.assertNotIn(token, repr(findings))

    def test_skips_symlinks_and_large_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / (root.name + "-outside")
            outside.write_text("token=" + ("x" * 40), encoding="utf-8")
            (root / "link").symlink_to(outside)
            (root / "large.txt").write_text("x" * 1_000_001, encoding="utf-8")
            findings, errors = scan(root)
        self.assertEqual(findings, [])
        self.assertEqual(errors, [])
        outside.unlink()

if __name__ == "__main__":
    unittest.main()
