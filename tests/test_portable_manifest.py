"""Tests for deterministic unsigned portable release manifests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.portable_manifest import (
    create_unsigned_manifest,
    inventory_bundle,
    write_unsigned_manifest,
)
from scripts.portable_runtime_inputs import InputValidationError
from yuki_trust.portable_bootstrap import _validate_manifest


class PortableManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "bundle"
        self.root.mkdir()
        (self.root / "bin").mkdir()
        (self.root / "bin" / "yuki").write_bytes(b"#!/bin/sh\necho yuki\n")
        (self.root / "README.txt").write_text("portable", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_matches_bootstrap_schema_and_inventory(self):
        value = create_unsigned_manifest(
            self.root, platform="linux-x86_64", runtime_version="0.1.0"
        )
        self.assertEqual(value["artifact_kind"], "unsigned-portable-runtime")
        self.assertEqual(value["files"], inventory_bundle(self.root))
        self.assertEqual(len(_validate_manifest(value)), 2)

    def test_write_is_deterministic_and_excludes_metadata_files(self):
        first = write_unsigned_manifest(
            self.root, platform="linux-x86_64", runtime_version="0.1.0"
        )
        payload = (self.root / "runtime-manifest.json").read_bytes()
        value = json.loads(payload)
        self.assertEqual(first["file_count"], 2)
        self.assertFalse(first["signed"])
        self.assertEqual([item["path"] for item in value["files"]], ["README.txt", "bin/yuki"])

    def test_refuses_existing_manifest_or_signature(self):
        (self.root / "runtime-manifest.sig").write_text("not signed", encoding="ascii")
        with self.assertRaisesRegex(InputValidationError, "already exists"):
            write_unsigned_manifest(
                self.root, platform="linux-x86_64", runtime_version="0.1.0"
            )

    def test_rejects_symlink_files(self):
        link = self.root / "linked"
        try:
            link.symlink_to(self.root / "README.txt")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable on this host.")
        with self.assertRaisesRegex(InputValidationError, "non-regular"):
            inventory_bundle(self.root)

    def test_rejects_backslash_in_manifest_path(self):
        from scripts.portable_manifest import _validate_relative_path
        with self.assertRaisesRegex(InputValidationError, "non-canonical"):
            _validate_relative_path("folder\\\\evil")

    def test_rejects_symlinked_bundle_root(self):
        alias = Path(self.temp.name) / "bundle-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable on this host.")
        with self.assertRaisesRegex(InputValidationError, "non-symlink"):
            inventory_bundle(alias)

    def test_rejects_windows_reserved_and_nonportable_paths(self):
        (self.root / "CON.txt").write_text("reserved", encoding="utf-8")
        with self.assertRaisesRegex(InputValidationError, "not portable"):
            inventory_bundle(self.root)

    def test_rejects_empty_metadata(self):
        with self.assertRaises(InputValidationError):
            create_unsigned_manifest(self.root, platform="", runtime_version="0.1.0")


if __name__ == "__main__":
    unittest.main()
