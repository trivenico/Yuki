"""Tests for non-executing skill discovery."""
import tempfile
import unittest
from pathlib import Path

from core.skill_discovery import SkillDiscovery


class SkillDiscoveryTests(unittest.TestCase):
    def test_discovers_candidate_without_importing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = root / "skills"
            skills.mkdir()
            marker = root / "executed.txt"
            (skills / "sample.py").write_text(
                "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('ran')\n",
                encoding="utf-8",
            )
            discovery = SkillDiscovery(skills)
            result = discovery.scan()
            self.assertEqual([item["file"] for item in result["pending_review"]], ["sample.py"])
            self.assertFalse(marker.exists())
            self.assertEqual(discovery.scan()["unchanged"][0]["file"], "sample.py")

    def test_changed_skill_returns_new_hash_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skills.mkdir()
            path = skills / "demo.py"
            path.write_text("value = 1\n", encoding="utf-8")
            discovery = SkillDiscovery(skills)
            first = discovery.scan()["pending_review"][0]["sha256"]
            path.write_text("value = 2\n", encoding="utf-8")
            result = discovery.scan()["pending_review"][0]
            self.assertNotEqual(first, result["sha256"])

    def test_rejects_symlink_and_oversized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = root / "skills"
            skills.mkdir()
            outside = root / "outside.py"
            outside.write_text("pass\n", encoding="utf-8")
            (skills / "large.py").write_text("x" * 100, encoding="utf-8")
            try:
                (skills / "linked.py").symlink_to(outside)
            except (OSError, NotImplementedError):
                pass
            result = SkillDiscovery(skills, max_file_bytes=20).scan()
            self.assertIn("large.py", [item["file"] for item in result["rejected"]])
            if (skills / "linked.py").is_symlink():
                self.assertIn("linked.py", [item["file"] for item in result["rejected"]])

    def test_rejects_invalid_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skills.mkdir()
            (skills / "bad-name.py").write_text("pass\n", encoding="utf-8")
            result = SkillDiscovery(skills).scan()
            self.assertIn("bad-name.py", [item["file"] for item in result["rejected"]])


if __name__ == "__main__":
    unittest.main()
