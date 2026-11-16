import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.desktop_artifact_manifest import build_manifest


class DesktopArtifactManifestTests(unittest.TestCase):
    def test_manifest_hashes_files_with_relative_paths_and_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Yuki"
            root.mkdir()
            (root / "Yuki.exe").write_bytes(b"desktop-binary")
            nested = root / "runtime"
            nested.mkdir()
            (nested / "config.json").write_text('{"safe": true}', encoding="utf-8")

            manifest = build_manifest(root, commit="abc123", ref="refs/heads/release-test")

            self.assertEqual(manifest["source_commit"], "abc123")
            self.assertEqual(manifest["source_ref"], "refs/heads/release-test")
            self.assertEqual(manifest["artifact_kind"], "unsigned-developer-bundle")
            self.assertEqual(
                [entry["path"] for entry in manifest["files"]],
                ["Yuki.exe", "runtime/config.json"],
            )
            self.assertEqual(
                manifest["files"][0]["sha256"],
                hashlib.sha256(b"desktop-binary").hexdigest(),
            )
            self.assertEqual(manifest["files"][0]["size"], len(b"desktop-binary"))
            self.assertIn("mode", manifest["files"][0])
            self.assertNotIn(str(root), json.dumps(manifest))

    def test_manifest_rejects_missing_or_empty_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty"
            empty.mkdir()
            with self.assertRaises(ValueError):
                build_manifest(empty)


if __name__ == "__main__":
    unittest.main()
