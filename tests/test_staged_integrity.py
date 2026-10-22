import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core.staged_integrity import StagedIntegrityError, verify_staged_workspace


class StagedIntegrityTests(unittest.TestCase):
    def test_accepts_matching_candidate_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = "def test_ok(): pass\n"
            path = root / "tests" / "test_candidate.py"
            path.parent.mkdir()
            path.write_text(content, encoding="utf-8")
            digest = hashlib.sha256()
            digest.update(b"tests/test_candidate.py")
            digest.update(b"\\0")
            digest.update(content.encode())
            digest.update(b"\\0")
            proposal_sha = digest.hexdigest()
            (root / "MANIFEST.json").write_text(json.dumps({
                "schema_version": 1, "proposal_sha256": proposal_sha,
                "files": [{"path": "tests/test_candidate.py",
                           "sha256": hashlib.sha256(content.encode()).hexdigest(),
                           "size_bytes": len(content.encode())}]
            }), encoding="utf-8")
            self.assertEqual(verify_staged_workspace(root), proposal_sha)
            path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(StagedIntegrityError):
                verify_staged_workspace(root)

    def test_rejects_unexpected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "MANIFEST.json").write_text(json.dumps({
                "schema_version": 1, "proposal_sha256": "a" * 64,
                "files": [{"path": "x.py", "sha256": hashlib.sha256(b"x").hexdigest(), "size_bytes": 1}]
            }), encoding="utf-8")
            (root / "x.py").write_text("x", encoding="utf-8")
            (root / "extra.py").write_text("unexpected", encoding="utf-8")
            with self.assertRaises(StagedIntegrityError):
                verify_staged_workspace(root)


if __name__ == "__main__":
    unittest.main()
