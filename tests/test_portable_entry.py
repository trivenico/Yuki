"""Tests for portable application path and native loader activation."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import portable_entry
from portable_entry import PortableEntryError, configure_bundle_paths, configure_native_libraries


class PortableEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "bundle"
        (self.root / "runtime").mkdir(parents=True)
        (self.root / "site-packages").mkdir()
        (self.root / "native-libs").mkdir()

    def tearDown(self):
        portable_entry._DLL_DIRECTORY_HANDLES.clear()
        portable_entry._REGISTERED_DLL_DIRECTORIES.clear()
        self.temp.cleanup()

    def test_adds_only_bundle_paths_in_deterministic_order(self):
        paths = ["", "/host/site-packages", "/stdlib"]
        result = configure_bundle_paths(self.root, paths)
        self.assertEqual(result[:2], [str((self.root / "site-packages").resolve()), str(self.root.resolve())])
        self.assertEqual(result[2:], ["", "/host/site-packages", "/stdlib"])

    def test_is_idempotent(self):
        paths = []
        configure_bundle_paths(self.root, paths)
        configure_bundle_paths(self.root, paths)
        self.assertEqual(paths.count(str(self.root.resolve())), 1)
        self.assertEqual(paths.count(str((self.root / "site-packages").resolve())), 1)

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

    def test_windows_registers_only_runtime_and_native_libs_and_retains_handles(self):
        calls = []
        class Handle: pass
        def register(path):
            calls.append(path)
            return Handle()
        expected = [str((self.root / "runtime").resolve()), str((self.root / "native-libs").resolve())]
        first = configure_native_libraries(self.root, platform_name="nt", add_dll_directory=register)
        second = configure_native_libraries(self.root, platform_name="nt", add_dll_directory=register)
        self.assertEqual(first, expected)
        self.assertEqual(second, [])
        self.assertEqual(calls, expected)
        self.assertEqual(len(portable_entry._DLL_DIRECTORY_HANDLES), 2)

    def test_windows_requires_runtime_directory(self):
        (self.root / "runtime").rmdir()
        with self.assertRaisesRegex(PortableEntryError, "runtime"):
            configure_native_libraries(self.root, platform_name="nt", add_dll_directory=lambda path: object())

    def test_windows_rejects_symlinked_native_libs(self):
        target = self.root / "external-native"
        target.mkdir()
        (self.root / "native-libs").rmdir()
        try:
            (self.root / "native-libs").symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks unavailable")
        with self.assertRaisesRegex(PortableEntryError, "native-libs"):
            configure_native_libraries(self.root, platform_name="nt", add_dll_directory=lambda path: object())

    def test_non_windows_does_not_mutate_native_loader(self):
        def unexpected(_path):
            self.fail("DLL registration must not run outside Windows")
        self.assertEqual(configure_native_libraries(self.root, platform_name="posix", add_dll_directory=unexpected), [])

    def test_isolated_subprocess_launch_uses_bundled_main_not_host_paths(self):
        entrypoint = self.root / "portable_entry.py"
        source = Path(__file__).resolve().parents[1] / "portable_entry.py"
        entrypoint.write_bytes(source.read_bytes())
        marker = self.root.parent / "launched.txt"
        bundled_main = self.root / "site-packages" / "main.py"
        bundled_main.write_text(
            "import os\nfrom pathlib import Path\n"
            "def main():\n    Path(os.environ['YUKI_SMOKE_MARKER']).write_text('bundled', encoding='utf-8')\n    return 0\n"
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
            [sys.executable, "-I", str(entrypoint)], cwd=hostile, env=env,
            capture_output=True, text=True, timeout=30, check=False,
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
