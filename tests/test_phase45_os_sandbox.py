"""Phase 45 tests for the Linux OS sandbox adapter contract."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.os_sandbox import LinuxBubblewrapExecutor, SandboxUnavailableError


class LinuxSandboxContractTests(unittest.TestCase):
    def test_rejects_non_linux_platform(self):
        with patch("core.os_sandbox.os.name", "nt"), patch("core.os_sandbox.sys.platform", "win32"):
            with self.assertRaisesRegex(SandboxUnavailableError, "only available on Linux"):
                LinuxBubblewrapExecutor(rootfs=".")

    def test_rejects_host_root_filesystem(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux-only contract test")
        with self.assertRaises(ValueError):
            LinuxBubblewrapExecutor(rootfs="/", bubblewrap_path="/bin/true")

    def test_requires_explicit_rootfs_marker(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux-only contract test")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "marker"):
                LinuxBubblewrapExecutor(rootfs=directory, bubblewrap_path="/bin/true")

    def test_rejects_group_or_world_writable_rootfs(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux-only contract test")
        with tempfile.TemporaryDirectory() as directory:
            rootfs = Path(directory) / "rootfs"
            rootfs.mkdir()
            (rootfs / ".yuki-sandbox-rootfs").write_text("explicit minimal rootfs\n", encoding="utf-8")
            rootfs.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "writable"):
                LinuxBubblewrapExecutor(rootfs=rootfs, bubblewrap_path="/bin/true")

    def test_rejects_missing_bubblewrap_binary(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux-only contract test")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".yuki-sandbox-rootfs").write_text("explicit minimal rootfs\n", encoding="utf-8")
            with patch("core.os_sandbox.shutil.which", return_value=None):
                with self.assertRaisesRegex(SandboxUnavailableError, "not installed"):
                    LinuxBubblewrapExecutor(rootfs=directory)


if __name__ == "__main__":
    unittest.main()
