"""Tests for transactional portable bundle assembly."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.portable_bundle import assemble_bundle
from scripts.portable_runtime_inputs import InputValidationError


class PortableBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime-input"
        self.dependencies = self.root / "dependencies-input"
        self.runtime.mkdir()
        self.dependencies.mkdir()
        (self.runtime / "bin").mkdir()
        (self.runtime / "bin" / "python").write_bytes(b"runtime")
        (self.dependencies / "yuki").mkdir()
        (self.dependencies / "yuki" / "__init__.py").write_text("APP = True\n", encoding="utf-8")
        self.destination = self.root / "release" / "yuki-portable"

    def tearDown(self):
        self.temp.cleanup()

    def test_assembles_separate_runtime_and_site_packages_layout(self):
        self.destination.parent.mkdir()
        report = assemble_bundle(self.runtime, self.dependencies, self.destination)
        self.assertEqual(report["total_files"], 2)
        self.assertTrue(report["atomic_publish"])
        self.assertEqual((self.destination / "runtime/bin/python").read_bytes(), b"runtime")
        self.assertEqual((self.destination / "site-packages/yuki/__init__.py").read_text(), "APP = True\n")
        self.assertFalse(report["signed"])

    def test_rejects_existing_destination_without_modifying_it(self):
        self.destination.parent.mkdir()
        self.destination.mkdir()
        with self.assertRaisesRegex(InputValidationError, "must not already exist"):
            assemble_bundle(self.runtime, self.dependencies, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_rejects_symlinked_source_directory(self):
        alias = self.root / "runtime-alias"
        try:
            alias.symlink_to(self.runtime, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        self.destination.parent.mkdir()
        with self.assertRaisesRegex(InputValidationError, "non-symlink"):
            assemble_bundle(alias, self.dependencies, self.destination)

    def test_rejects_symlinked_file_and_cleans_stage(self):
        target = self.runtime / "linked"
        try:
            target.symlink_to(self.runtime / "bin" / "python")
        except (OSError, NotImplementedError):
            self.skipTest("File symlinks unavailable")
        self.destination.parent.mkdir()
        with self.assertRaisesRegex(InputValidationError, "non-regular"):
            assemble_bundle(self.runtime, self.dependencies, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_rejects_destination_parent_inside_source(self):
        nested_parent = self.runtime / "output"
        nested_parent.mkdir()
        with self.assertRaisesRegex(InputValidationError, "must not overlap"):
            assemble_bundle(self.runtime, self.dependencies, nested_parent / "bundle")

    def test_rejects_empty_dependency_input_transactionally(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.destination.parent.mkdir()
        with self.assertRaisesRegex(InputValidationError, "must both contain files"):
            assemble_bundle(self.runtime, empty, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
