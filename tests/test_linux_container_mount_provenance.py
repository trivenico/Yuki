import unittest

from core.host_runtime_inventory import RuntimeInventoryError
from core.linux_container_mount_provenance import (
    parse_container_mountinfo,
    verify_image_owned_application_root,
)


def mountinfo(extra=""):
    lines = [
        "1 0 0:1 / / rw,relatime - overlay overlay rw",
        "2 1 0:2 / /workspace rw,relatime - ext4 /dev/vdb rw",
        "3 1 0:3 / /proc rw,nosuid - proc proc rw",
    ]
    if extra:
        lines.append(extra)
    return "\\n".join(lines) + "\\n"


class ContainerMountProvenanceTests(unittest.TestCase):
    def test_parses_mount_namespace_and_accepts_unmounted_image_app_root(self):
        mounts = parse_container_mountinfo(mountinfo())
        self.assertEqual(
            verify_image_owned_application_root("/app", mounts),
            "/app",
        )

    def test_rejects_workspace_and_descendants(self):
        mounts = parse_container_mountinfo(mountinfo())
        for path in ("/workspace", "/workspace/project", "/workspace/node_modules"):
            with self.subTest(path=path), self.assertRaises(RuntimeInventoryError):
                verify_image_owned_application_root(path, mounts)

    def test_rejects_nested_mount_inside_application_root(self):
        raw = mountinfo(
            "4 1 0:4 / /app/node_modules rw,relatime - tmpfs tmpfs rw"
        )
        with self.assertRaises(RuntimeInventoryError):
            verify_image_owned_application_root(
                "/app", parse_container_mountinfo(raw),
            )

    def test_rejects_application_root_that_is_mountpoint_or_contains_mount(self):
        mounts = parse_container_mountinfo(mountinfo())
        for path in ("/proc", "/"):
            with self.subTest(path=path), self.assertRaises(RuntimeInventoryError):
                verify_image_owned_application_root(path, mounts)

    def test_decodes_mountinfo_escaped_spaces(self):
        raw = mountinfo(
            "4 1 0:4 / /var/lib/my\\\\040app rw,relatime - ext4 /dev/vdc rw"
        )
        mounts = parse_container_mountinfo(raw)
        self.assertIn("/var/lib/my app", {item.mountpoint for item in mounts})

    def test_rejects_duplicate_ids_missing_root_and_malformed_records(self):
        cases = (
            "1 0 0:1 / / rw - overlay overlay rw\\n"
            "1 0 0:2 / /workspace rw - ext4 /dev/vdb rw\\n",
            "2 1 0:2 / /workspace rw - ext4 /dev/vdb rw\\n",
            "1 0 0:1 / / rw,relatime overlay overlay rw\\n",
        )
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(RuntimeInventoryError):
                parse_container_mountinfo(raw)

    def test_requires_explicit_workspace_mount_exclusion(self):
        mounts = parse_container_mountinfo(mountinfo())
        with self.assertRaises(RuntimeInventoryError):
            verify_image_owned_application_root(
                "/app", mounts, excluded_task_mounts=("/tmp/task",),
            )

    def test_rejects_mountinfo_size_and_path_traversal(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_container_mountinfo("x" * (1024 * 1024 + 1))
        with self.assertRaises(RuntimeInventoryError):
            verify_image_owned_application_root(
                "/app/../workspace", parse_container_mountinfo(mountinfo()),
            )


if __name__ == "__main__":
    unittest.main()
