import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yuki_trust.portable_bootstrap import BootstrapError
from yuki_trust.portable_host import load_host_policy, launch_with_host_policy


class PortableHostPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "usb"
        self.bundle.mkdir()
        self.trust_dir = self.root / "host-trust"
        self.trust_dir.mkdir()
        self.public_key = self.trust_dir / "release-ed25519.pem"
        self.public_key.write_text("test public key", encoding="ascii")
        self.policy = self.trust_dir / "portable-policy.json"
        self.write_policy()

    def write_policy(self, **overrides):
        value = {
            "schema_version": 1,
            "public_key": str(self.public_key),
            "expected_platform": "linux-x86_64",
            "python": "runtime/bin/python3",
            "entrypoint": "portable_entry.py",
        }
        value.update(overrides)
        self.policy.write_text(json.dumps(value), encoding="utf-8")
        if os.name != "nt":
            self.policy.chmod(0o600)
            self.public_key.chmod(0o600)

    def test_loads_host_owned_policy_and_pins_launch_parameters(self):
        policy = load_host_policy(self.policy, self.bundle)
        self.assertEqual(policy["public_key"], str(self.public_key.resolve()))
        self.assertEqual(policy["expected_platform"], "linux-x86_64")
        self.assertEqual(policy["python"], "runtime/bin/python3")
        self.assertEqual(policy["entrypoint"], "portable_entry.py")

    def test_launch_delegates_only_host_policy_values(self):
        with patch("yuki_trust.portable_host.run_verified_bundle", return_value=17) as launch:
            result = launch_with_host_policy(self.bundle, self.policy, ["--diagnostic"])
        self.assertEqual(result, 17)
        launch.assert_called_once_with(
            self.bundle,
            str(self.public_key.resolve()),
            "runtime/bin/python3",
            "portable_entry.py",
            ["--diagnostic"],
            expected_platform="linux-x86_64",
        )

    def test_rejects_policy_inside_removable_bundle(self):
        inside = self.bundle / "policy.json"
        inside.write_text(self.policy.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "outside the removable bundle"):
            load_host_policy(inside, self.bundle)

    def test_rejects_trust_anchor_inside_removable_bundle(self):
        inside = self.bundle / "release.pem"
        inside.write_text("not a real key", encoding="ascii")
        self.write_policy(public_key=str(inside))
        with self.assertRaisesRegex(BootstrapError, "outside the removable bundle"):
            load_host_policy(self.policy, self.bundle)

    def test_rejects_relative_trust_anchor(self):
        self.write_policy(public_key="release-ed25519.pem")
        with self.assertRaisesRegex(BootstrapError, "must be absolute"):
            load_host_policy(self.policy, self.bundle)

    def test_rejects_noncanonical_bundle_paths(self):
        for field, value in (
            ("python", "../python"),
            ("python", r"runtime\\python.exe"),
            ("entrypoint", "/portable_entry.py"),
            ("entrypoint", "folder//entry.py"),
        ):
            with self.subTest(field=field, value=value):
                self.write_policy(**{field: value})
                with self.assertRaises(BootstrapError):
                    load_host_policy(self.policy, self.bundle)

    def test_rejects_unknown_or_missing_policy_fields(self):
        self.write_policy(extra="not allowed")
        with self.assertRaisesRegex(BootstrapError, "invalid schema"):
            load_host_policy(self.policy, self.bundle)
        self.write_policy(entrypoint=None)
        with self.assertRaisesRegex(BootstrapError, "must be non-empty text"):
            load_host_policy(self.policy, self.bundle)

    def test_rejects_duplicate_json_keys(self):
        self.policy.write_text(
            '{"schema_version":1,"schema_version":1}',
            encoding="utf-8",
        )
        if os.name != "nt":
            self.policy.chmod(0o600)
        with self.assertRaisesRegex(BootstrapError, "duplicate JSON keys"):
            load_host_policy(self.policy, self.bundle)

    def test_rejects_symlinked_policy(self):
        link = self.trust_dir / "policy-link.json"
        try:
            link.symlink_to(self.policy)
        except (OSError, NotImplementedError):
            self.skipTest("file symlinks unavailable")
        with self.assertRaisesRegex(BootstrapError, "non-symlink"):
            load_host_policy(link, self.bundle)

    @unittest.skipIf(os.name == "nt", "POSIX directory mode validation is platform-specific")
    def test_rejects_group_or_world_writable_parent_directory(self):
        unsafe = self.root / "unsafe-host-dir"
        unsafe.mkdir()
        unsafe.chmod(0o777)
        policy = unsafe / "policy.json"
        policy.write_text(self.policy.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "parent directories must not be group- or world-writable"):
            load_host_policy(policy, self.bundle)

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_rejects_symlinked_parent_directory(self):
        real = self.root / "real-host-dir"
        real.mkdir()
        policy = real / "policy.json"
        policy.write_text(self.policy.read_text(encoding="utf-8"), encoding="utf-8")
        link = self.root / "linked-host-dir"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks unavailable")
        with self.assertRaisesRegex(BootstrapError, "parent directories must be real directories"):
            load_host_policy(link / "policy.json", self.bundle)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not Windows ACL validation")
    def test_rejects_group_or_world_writable_policy(self):
        self.policy.chmod(0o620)
        with self.assertRaisesRegex(BootstrapError, "must not be group- or world-writable"):
            load_host_policy(self.policy, self.bundle)


if __name__ == "__main__":
    unittest.main()
