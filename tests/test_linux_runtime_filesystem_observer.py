import os
import tempfile
import unittest
from pathlib import Path

from core.host_runtime_inventory import RuntimeInventoryError
from core.linux_runtime_filesystem_observer import LinuxRuntimeFilesystemObserver


class LinuxRuntimeFilesystemObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proc = self.root / "proc"
        self.container_id = "a" * 64
        self.pid = self.proc / "4321"
        self.pid.mkdir(parents=True)
        (self.pid / "cgroup").write_text(
            "0::/system.slice/docker-" + self.container_id + ".scope\n",
            encoding="ascii",
        )
        image_root = self.root / "container-root"
        python_lib = image_root / "usr/local/lib/python3.12"
        config = python_lib / "config-3.12-x86_64-linux-gnu"
        site = python_lib / "site-packages"
        dist = site / "requests-2.32.3.dist-info"
        config.mkdir(parents=True)
        dist.mkdir(parents=True)
        (config / "Makefile").write_text("VERSION=3.12.3\n", encoding="ascii")
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: requests\nVersion: 2.32.3\n",
            encoding="utf-8",
        )
        (self.pid / "root").symlink_to(image_root, target_is_directory=True)
        self.observer = LinuxRuntimeFilesystemObserver(proc_root=str(self.proc))

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_runtime_and_package_metadata_without_container_execution(self):
        result = self.observer(
            "task-1", "worker-1", self.container_id,
            "python@sha256:" + "b" * 64, "spec-1",
        )
        self.assertEqual(result["runtime_versions"], {"python": "3.12.3"})
        self.assertEqual(result["installed_dependencies"], {"requests": "2.32.3"})

    def test_reads_installed_debian_packages_from_status_database(self):
        status = self.root / "container-root/var/lib/dpkg/status"
        status.parent.mkdir(parents=True)
        status.write_text(
            "Package: libc6\nStatus: install ok installed\nVersion: 2.36-9+deb12u4\n\n"
            "Package: removed-pkg\nStatus: deinstall ok config-files\nVersion: 1.0\n",
            encoding="utf-8",
        )
        result = self.observer(
            "task-1", "worker-1", self.container_id,
            "python@sha256:" + "b" * 64, "spec-1",
        )
        self.assertEqual(result["installed_dependencies"]["deb:libc6"], "2.36-9+deb12u4")
        self.assertNotIn("deb:removed-pkg", result["installed_dependencies"])

    def test_rejects_malformed_installed_debian_package_record(self):
        status = self.root / "container-root/var/lib/dpkg/status"
        status.parent.mkdir(parents=True)
        status.write_text(
            "Package: broken-pkg\nStatus: install ok installed\n\n",
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_requires_exact_container_cgroup_identity(self):
        (self.pid / "cgroup").write_text("0::/system.slice/docker-" + "c" * 64 + ".scope\n")
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_rejects_missing_python_runtime_metadata(self):
        (self.pid / "cgroup").write_text(
            "0::/system.slice/docker-" + self.container_id + ".scope\n",
            encoding="ascii",
        )
        python_dir = self.root / "container-root/usr/local/lib/python3.12"
        (python_dir / "config-3.12-x86_64-linux-gnu/Makefile").unlink()
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_rejects_incomplete_or_symlinked_distribution_metadata(self):
        dist = self.root / "container-root/usr/local/lib/python3.12/site-packages/requests-2.32.3.dist-info"
        (dist / "METADATA").unlink()
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

        dist.rmdir()
        outside = self.root / "outside-dist-info"
        outside.mkdir()
        (outside / "METADATA").write_text(
            "Name: requests\nVersion: 2.32.3\n", encoding="utf-8",
        )
        dist.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_rejects_metadata_without_name_or_version(self):
        dist = self.root / "container-root/usr/local/lib/python3.12/site-packages/requests-2.32.3.dist-info"
        (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: requests\n", encoding="utf-8")
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_rejects_unbounded_process_scan_configuration(self):
        with self.assertRaises(ValueError):
            LinuxRuntimeFilesystemObserver(proc_root=str(self.proc), max_processes=100000)


if __name__ == "__main__":
    unittest.main()
