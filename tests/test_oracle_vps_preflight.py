import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import oracle_vps_preflight as preflight


def _resource_inventory():
    return SimpleNamespace(
        logical_cpu_count=4,
        effective_cpu_count=1,
        cpu_cgroup_limit=1.5,
        cpu_cgroup_source="cgroup_v2",
        memory_total_bytes=24 * 1024**3,
        memory_available_bytes=12 * 1024**3,
        memory_cgroup_limit_bytes=8 * 1024**3,
        memory_cgroup_available_bytes=4 * 1024**3,
        memory_cgroup_source="cgroup_v2",
        memory_effective_available_bytes=4 * 1024**3,
    )


class OracleVpsPreflightTests(unittest.TestCase):
    def test_report_is_read_only_and_exposes_host_and_effective_resource_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with patch.object(preflight, "_read_os_release", return_value={
                "ID": "ubuntu",
                "VERSION_ID": "24.04",
                "PRETTY_NAME": "Ubuntu 24.04 LTS",
            }), patch.object(preflight.platform, "machine", return_value="aarch64"), patch.object(
                preflight.sys, "platform", "linux",
            ), patch.object(preflight, "ResourceInventory") as inventory, patch.object(
                preflight.shutil, "which", return_value="/usr/bin/git",
            ):
                inventory.detect.return_value = _resource_inventory()
                report = preflight.collect_report(target=target)

            self.assertTrue(report["read_only"])
            self.assertTrue(report["no_packages_installed"])
            self.assertTrue(report["no_network_requests"])
            self.assertEqual(report["profile"], "oracle_cloud_ubuntu_24_04_arm64")
            self.assertEqual(report["platform"]["architecture"], "aarch64")
            self.assertEqual(report["resources"]["logical_cpu_count"], 4)
            self.assertEqual(report["resources"]["effective_cpu_count"], 1)
            self.assertEqual(report["resources"]["cpu_cgroup_source"], "cgroup_v2")
            self.assertEqual(report["resources"]["memory_effective_available_bytes"], 4 * 1024**3)
            self.assertGreater(report["resources"]["target_disk_free_bytes"], 0)
            self.assertTrue(report["checks"]["ubuntu_24_04"])
            self.assertTrue(report["checks"]["arm64"])
            self.assertTrue(report["checks"]["target_directory_writable"])
            self.assertTrue(report["checks"]["target_disk_capacity_available"])
            self.assertIn("cgroup limits", " ".join(report["notes"]))

    def test_missing_target_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "does-not-exist"
            with patch.object(preflight, "_read_os_release", return_value={}), patch.object(
                preflight.platform, "machine", return_value="x86_64",
            ), patch.object(preflight.sys, "platform", "linux"), patch.object(
                preflight, "ResourceInventory",
            ) as inventory:
                inventory.detect.return_value = _resource_inventory()
                report = preflight.collect_report(target=target)
        self.assertFalse(report["ready"])
        self.assertFalse(report["checks"]["target_directory_exists"])
        self.assertFalse(report["checks"]["arm64"])
        self.assertFalse(report["checks"]["ubuntu_24_04"])

    def test_unavailable_target_disk_capacity_prevents_ready_status(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with patch.object(preflight, "_read_os_release", return_value={
                "ID": "ubuntu",
                "VERSION_ID": "24.04",
                "PRETTY_NAME": "Ubuntu 24.04 LTS",
            }), patch.object(preflight.platform, "machine", return_value="aarch64"), patch.object(
                preflight.sys, "platform", "linux",
            ), patch.object(preflight, "ResourceInventory") as inventory, patch.object(
                preflight.shutil, "disk_usage", side_effect=OSError("statvfs failed"),
            ):
                inventory.detect.return_value = _resource_inventory()
                report = preflight.collect_report(target=target)

        self.assertIsNone(report["resources"]["target_disk_free_bytes"])
        self.assertFalse(report["checks"]["target_disk_capacity_available"])
        self.assertFalse(report["ready"])

    def test_os_release_parser_handles_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            values = preflight._read_os_release(Path(directory) / "missing")
        self.assertEqual(values, {})


if __name__ == "__main__":
    unittest.main()
