import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
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
        (self.pid / "mountinfo").write_text(
            "1 0 0:1 / / rw,relatime - overlay overlay rw\n"
            "2 1 0:2 / /workspace rw,relatime - ext4 /dev/vdb rw\n"
            "3 1 0:3 / /proc rw,nosuid - proc proc rw\n",
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


    def test_never_inventories_python_packages_from_workspace_mount(self):
        workspace_site = self.root / "container-root/workspace/lib/python3.11/site-packages"
        dist = workspace_site / "task-only-package-1.0.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: task-only-package\nVersion: 1.0\n",
            encoding="utf-8",
        )
        result = self.observer(
            "task-1", "worker-1", self.container_id,
            "python@sha256:" + "b" * 64, "spec-1",
        )
        self.assertNotIn("task-only-package", result["installed_dependencies"])

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

    def test_reads_node_runtime_and_scoped_nested_npm_packages(self):
        image = self.root / "container-root"
        header = image / "usr/include/node/node_version.h"
        header.parent.mkdir(parents=True)
        header.write_text(
            "#define NODE_MAJOR_VERSION 22\n"
            "#define NODE_MINOR_VERSION 4\n"
            "#define NODE_PATCH_VERSION 1\n",
            encoding="ascii",
        )
        modules = image / "usr/lib/node_modules"
        (modules / "alpha/node_modules/dep").mkdir(parents=True)
        (modules / "@scope/pkg").mkdir(parents=True)
        (modules / "alpha/package.json").write_text(
            '{"name":"alpha","version":"1.2.3"}', encoding="utf-8",
        )
        (modules / "alpha/node_modules/dep/package.json").write_text(
            '{"name":"dep","version":"4.5.6"}', encoding="utf-8",
        )
        (modules / "@scope/pkg/package.json").write_text(
            '{"name":"@scope/pkg","version":"7.8.9"}', encoding="utf-8",
        )
        result = self.observer(
            "task-1", "worker-1", self.container_id,
            "python@sha256:" + "b" * 64, "spec-1",
        )
        self.assertEqual(result["runtime_versions"]["nodejs"], "22.4.1")
        self.assertEqual(result["installed_dependencies"]["npm:alpha#usr/lib/node_modules/alpha"], "1.2.3")
        self.assertEqual(result["installed_dependencies"]["npm:dep#usr/lib/node_modules/alpha/node_modules/dep"], "4.5.6")
        self.assertEqual(result["installed_dependencies"]["npm:@scope/pkg#usr/lib/node_modules/@scope/pkg"], "7.8.9")

    def test_supports_node_only_container_without_python_runtime(self):
        image = self.root / "container-root"
        shutil.rmtree(image / "usr/local/lib/python3.12")
        header = image / "usr/include/node/node_version.h"
        header.parent.mkdir(parents=True)
        header.write_text(
            "#define NODE_MAJOR_VERSION 22\n"
            "#define NODE_MINOR_VERSION 4\n"
            "#define NODE_PATCH_VERSION 1\n",
            encoding="ascii",
        )
        modules = image / "usr/lib/node_modules"
        (modules / "only-node").mkdir(parents=True)
        (modules / "only-node/package.json").write_text(
            '{"name":"only-node","version":"1.0.0"}', encoding="utf-8",
        )
        result = self.observer(
            "task-1", "worker-1", self.container_id,
            "node@sha256:" + "b" * 64, "spec-1",
        )
        self.assertEqual(result["runtime_versions"], {"nodejs": "22.4.1"})
        self.assertEqual(result["installed_dependencies"]["npm:only-node#usr/lib/node_modules/only-node"], "1.0.0")

    def test_rejects_symlinked_global_npm_package(self):
        image = self.root / "container-root"
        modules = image / "usr/lib/node_modules"
        modules.mkdir(parents=True)
        outside = self.root / "outside-npm"
        outside.mkdir()
        (outside / "package.json").write_text(
            '{"name":"unsafe","version":"1.0.0"}', encoding="utf-8",
        )
        (modules / "unsafe").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_rejects_symlinked_nested_npm_modules_directory(self):
        image = self.root / "container-root"
        modules = image / "usr/lib/node_modules"
        package = modules / "alpha"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            '{"name":"alpha","version":"1.0.0"}', encoding="utf-8",
        )
        outside = self.root / "outside-modules"
        outside.mkdir()
        (package / "node_modules").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_preserves_distinct_nested_npm_versions_by_install_path(self):
        image = self.root / "container-root"
        modules = image / "usr/lib/node_modules"
        (modules / "alpha/node_modules/shared").mkdir(parents=True)
        (modules / "beta/node_modules/shared").mkdir(parents=True)
        (modules / "alpha/package.json").write_text(
            '{"name":"alpha","version":"1.0.0"}', encoding="utf-8",
        )
        (modules / "beta/package.json").write_text(
            '{"name":"beta","version":"1.0.0"}', encoding="utf-8",
        )
        (modules / "alpha/node_modules/shared/package.json").write_text(
            '{"name":"shared","version":"1.0.0"}', encoding="utf-8",
        )
        (modules / "beta/node_modules/shared/package.json").write_text(
            '{"name":"shared","version":"2.0.0"}', encoding="utf-8",
        )
        result = self.observer(
            "task-1", "worker-1", self.container_id,
            "python@sha256:" + "b" * 64, "spec-1",
        )
        self.assertEqual(
            result["installed_dependencies"]["npm:shared#usr/lib/node_modules/alpha/node_modules/shared"],
            "1.0.0",
        )
        self.assertEqual(
            result["installed_dependencies"]["npm:shared#usr/lib/node_modules/beta/node_modules/shared"],
            "2.0.0",
        )

    def test_rejects_npm_package_path_name_mismatch(self):
        image = self.root / "container-root"
        package = image / "usr/lib/node_modules/expected"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            '{"name":"different","version":"1.0.0"}', encoding="utf-8",
        )
        with self.assertRaises(RuntimeInventoryError):
            self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )

    def test_reconciles_application_lockfile_with_image_owned_node_modules(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        package = app / "node_modules/alpha"
        package.mkdir(parents=True)
        (app / "package-lock.json").write_text(
            '{"lockfileVersion":3,"packages":{"":{"name":"yuki","version":"1.0.0"},'
            '"node_modules/alpha":{"version":"1.2.3"}}}',
            encoding="utf-8",
        )
        (package / "package.json").write_text(
            '{"name":"alpha","version":"1.2.3"}', encoding="utf-8",
        )
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            result = self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )
        self.assertEqual(
            result["installed_dependencies"]["npm:alpha#app/node_modules/alpha"],
            "1.2.3",
        )

    def test_rejects_application_lockfile_and_installed_tree_mismatch(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        package = app / "node_modules/alpha"
        package.mkdir(parents=True)
        (app / "package-lock.json").write_text(
            '{"lockfileVersion":3,"packages":{"node_modules/alpha":{"version":"1.2.3"}}}',
            encoding="utf-8",
        )
        (package / "package.json").write_text(
            '{"name":"alpha","version":"9.9.9"}', encoding="utf-8",
        )
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            with self.assertRaises(RuntimeInventoryError):
                self.observer(
                    "task-1", "worker-1", self.container_id,
                    "python@sha256:" + "b" * 64, "spec-1",
                )

    def test_rejects_application_root_inside_task_workspace(self):
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/workspace/yuki"}):
            with self.assertRaises(RuntimeInventoryError):
                self.observer(
                    "task-1", "worker-1", self.container_id,
                    "python@sha256:" + "b" * 64, "spec-1",
                )


    def test_reconciles_poetry_lock_with_application_virtualenv(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        site = app / ".venv/lib/python3.12/site-packages"
        dist = site / "requests-2.32.3.dist-info"
        dist.mkdir(parents=True)
        (app / "poetry.lock").write_text(
            'metadata = { lock-version = "2.0" }\n'
            '[[package]]\nname = "requests"\nversion = "2.32.3"\n',
            encoding="utf-8",
        )
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: requests\nVersion: 2.32.3\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            result = self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )
        self.assertEqual(result["installed_dependencies"]["requests"], "2.32.3")

    def test_reconciles_pinned_requirements_with_application_virtualenv(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        site = app / "venv/lib/python3.12/site-packages"
        dist = site / "typing_extensions-4.12.2.dist-info"
        dist.mkdir(parents=True)
        (app / "requirements.txt").write_text(
            "typing_extensions==4.12.2\n", encoding="utf-8",
        )
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: typing_extensions\nVersion: 4.12.2\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            result = self.observer(
                "task-1", "worker-1", self.container_id,
                "python@sha256:" + "b" * 64, "spec-1",
            )
        self.assertEqual(result["installed_dependencies"]["typing-extensions"], "4.12.2")

    def test_rejects_application_python_lock_and_virtualenv_mismatch(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        site = app / ".venv/lib/python3.12/site-packages"
        dist = site / "requests-2.32.3.dist-info"
        dist.mkdir(parents=True)
        (app / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "requests"\nversion = "2.32.3"\n',
            encoding="utf-8",
        )
        (dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: requests\nVersion: 9.9.9\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            with self.assertRaises(RuntimeInventoryError):
                self.observer(
                    "task-1", "worker-1", self.container_id,
                    "python@sha256:" + "b" * 64, "spec-1",
                )

    def test_rejects_ambiguous_python_lock_sources_and_multiple_venvs(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        (app / "poetry.lock").parent.mkdir(parents=True)
        (app / "poetry.lock").write_text(
            'metadata = { lock-version = "2.0" }\n[[package]]\nname = "demo"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        (app / "uv.lock").write_text(
            'version = 1\n[[package]]\nname = "demo"\nversion = "1.0"\n',
            encoding="utf-8",
        )
        site1 = app / ".venv/lib/python3.12/site-packages"
        site2 = app / "venv/lib/python3.12/site-packages"
        site1.mkdir(parents=True)
        site2.mkdir(parents=True)
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            with self.assertRaises(RuntimeInventoryError):
                self.observer(
                    "task-1", "worker-1", self.container_id,
                    "python@sha256:" + "b" * 64, "spec-1",
                )


    def test_rejects_multiple_application_virtualenvs(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        (app / "requirements.txt").write_text("requests==2.32.3\\n", encoding="utf-8")
        (app / ".venv/lib/python3.12/site-packages").mkdir(parents=True)
        (app / "venv/lib/python3.12/site-packages").mkdir(parents=True)
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
            with self.assertRaises(RuntimeInventoryError):
                self.observer(
                    "task-1", "worker-1", self.container_id,
                    "python@sha256:" + "b" * 64, "spec-1",
                )

    def test_rejects_symlinked_application_virtualenv(self):
        image = self.root / "container-root"
        app = image / "opt/yuki"
        app.mkdir(parents=True)
        outside = self.root / "outside-venv"
        (outside / "lib/python3.12/site-packages").mkdir(parents=True)
        (app / "requirements.txt").write_text("requests==2.32.3\\n", encoding="utf-8")
        (app / ".venv").symlink_to(outside, target_is_directory=True)
        with patch.dict(os.environ, {"YUKI_HOST_INVENTORY_APPLICATION_ROOT": "/opt/yuki"}):
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
