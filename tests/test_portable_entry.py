"""Tests for portable application path activation."""
from __future__ import annotations

import os
import subprocess
import sys
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

    def test_isolated_subprocess_launch_uses_bundled_main_not_host_paths(self):
        entrypoint = self.root / "portable_entry.py"
        source = Path(__file__).resolve().parents[1] / "portable_entry.py"
        entrypoint.write_bytes(source.read_bytes())
        marker = self.root.parent / "launched.txt"
        bundled_main = self.root / "site-packages" / "main.py"
        bundled_main.write_text(
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['YUKI_SMOKE_MARKER']).write_text('bundled', encoding='utf-8')\n"
        )
        hostile = self.root.parent / "hostile"
        hostile.mkdir()
        (hostile / "main.py").write_text(
            "from pathlib import Path\nPath('HOST_PATH_WAS_USED').write_text('bad')\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(hostile)
        env["PYTHONHOME"] = str(self.root.parent / "invalid-python-home")
        env["YUKI_SMOKE_MARKER"] = str(marker)
        completed = subprocess.run(
            [sys.executable, "-I", str(entrypoint)],
            cwd=hostile,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "bundled")
        self.assertFalse((hostile / "HOST_PATH_WAS_USED").exists())

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
