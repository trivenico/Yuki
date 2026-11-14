"""Tests for portable application path activation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable_entry import PortableEntryError, configure_bundle_paths


class PortableEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "bundle"
        (self.root / "runtime").mkdir(parents=True)
        (self.root / "site-packages").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_adds_only_bundle_paths_in_deterministic_order(self):
        paths = ["", "/host/site-packages", "/stdlib"]
        result = configure_bundle_paths(self.root, paths)
        self.assertEqual(result[:2], [str(self.root / "site-packages"), str(self.root)])
        self.assertEqual(result[2:], ["", "/host/site-packages", "/stdlib"])

    def test_is_idempotent(self):
        paths = []
        configure_bundle_paths(self.root, paths)
        configure_bundle_paths(self.root, paths)
        self.assertEqual(paths.count(str(self.root)), 1)
        self.assertEqual(paths.count(str(self.root / "site-packages")), 1)

    def test_rejects_missing_site_packages(self):
        (self.root / "site-packages").rmdir()
        with self.assertRaisesRegex(PortableEntryError, "site-packages"):
            configure_bundle_paths(self.root, [])

    def test_rejects_symlinked_site_packages(self):
        target = self.root / "external"
        target.mkdir()
        (self.root / "site-packages").rmdir()
        try:
            (self.root / "site-packages").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        with self.assertRaisesRegex(PortableEntryError, "non-symlink"):
            configure_bundle_paths(self.root, [])

    def test_rejects_symlinked_bundle_root(self):
        alias = self.root.parent / "bundle-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        with self.assertRaisesRegex(PortableEntryError, "bundle root"):
            configure_bundle_paths(alias, [])


if __name__ == "__main__":
    unittest.main()
