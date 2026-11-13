import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.portable_runtime_manifest import (
    MANIFEST_NAME,
    RuntimeManifestError,
    create_manifest,
    verify_manifest,
)


class PortableRuntimeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        (self.root / "python" / "bin").mkdir(parents=True)
        (self.root / "python" / "bin" / "python").write_bytes(b"runtime-binary")
        (self.root / "yuki").mkdir()
        (self.root / "yuki" / "app.py").write_text("print('Yuki')\n", encoding="utf-8")

    def _write_manifest(self):
        manifest = create_manifest(self.root, platform="linux-x86_64", runtime_version="3.12.7")
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_create_and_verify_manifest(self):
        manifest = self._write_manifest()
        verified = verify_manifest(self.root)
        self.assertEqual(verified, manifest)
        self.assertEqual(manifest["artifact_kind"], "unsigned-portable-runtime")
        self.assertEqual(len(manifest["files"]), 2)

    def test_modified_file_fails_integrity_check(self):
        self._write_manifest()
        target = self.root / "yuki" / "app.py"
        target.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "hashes"):
            verify_manifest(self.root)

    def test_added_file_fails_exact_inventory_check(self):
        self._write_manifest()
        (self.root / "unexpected.dll").write_bytes(b"extra")
        with self.assertRaisesRegex(RuntimeManifestError, "inventory"):
            verify_manifest(self.root)

    def test_manifest_path_traversal_is_rejected(self):
        manifest = self._write_manifest()
        manifest["files"][0]["path"] = "../escape"
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "escapes"):
            verify_manifest(self.root)

    def test_duplicate_manifest_paths_are_rejected(self):
        manifest = self._write_manifest()
        manifest["files"][1]["path"] = manifest["files"][0]["path"]
        manifest["files"].sort(key=lambda item: item["path"])
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "duplicate"):
            verify_manifest(self.root)

    def test_symlink_payload_is_rejected(self):
        self._write_manifest()
        link = self.root / "python" / "linked-python"
        try:
            link.symlink_to(self.root / "python" / "bin" / "python")
        except OSError:
            self.skipTest("symlink creation is not permitted")
        with self.assertRaisesRegex(RuntimeManifestError, "symbolic-link"):
            verify_manifest(self.root)

    def test_unsigned_manifest_does_not_claim_authenticity(self):
        manifest = self._write_manifest()
        self.assertEqual(manifest["artifact_kind"], "unsigned-portable-runtime")
        self.assertNotIn("signature", manifest)


if __name__ == "__main__":
    unittest.main()
