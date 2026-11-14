import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_portable_host as installer
from yuki_trust.portable_bootstrap import BootstrapError
from yuki_trust import portable_host


class PortableHostInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "release.pem"
        self.key.write_text("test-key", encoding="ascii")
        self.install = self.root / "trusted"
        self.state = self.root / "state"

    def _install(self):
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
            patch.object(installer, "_apply_windows_acl"),
        ):
            return installer.install_host_trust(self.key, self.install, self.state)

    def test_provisions_external_key_policy_state_and_launcher(self):
        result = self._install()
        policy_path = Path(result["policy"])
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(policy["public_key"], result["public_key"])
        self.assertEqual(policy["anti_rollback_state"], result["anti_rollback_state"])
        self.assertEqual(policy["entrypoint"], "portable_entry.py")
        self.assertTrue(Path(result["public_key"]).is_file())
        self.assertTrue(Path(result["launcher"]).is_file())
        self.assertFalse(Path(result["anti_rollback_state"]).exists())
        if os.name != "nt":
            self.assertEqual(policy_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.install.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_refuses_to_overwrite_existing_trust_anchor(self):
        self.install.mkdir()
        (self.install / installer._KEY_NAME).write_text("preexisting", encoding="ascii")
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
            patch.object(installer, "_apply_windows_acl"),
        ):
            with self.assertRaisesRegex(BootstrapError, "Refusing to overwrite"):
                installer.install_host_trust(self.key, self.install, self.state)
        self.assertEqual((self.install / installer._KEY_NAME).read_text(encoding="ascii"), "preexisting")

    def test_refuses_same_or_nested_trust_and_state_roots(self):
        with patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")):
            with self.assertRaisesRegex(BootstrapError, "must be separate"):
                installer.install_host_trust(self.key, self.install, self.install)
            with self.assertRaisesRegex(BootstrapError, "must be separate"):
                installer.install_host_trust(self.key, self.install, self.install / "state")

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_rejects_symlinked_install_root(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        with patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")):
            with self.assertRaisesRegex(BootstrapError, "real directory, not a symlink"):
                installer.install_host_trust(self.key, link, self.state)


if __name__ == "__main__":
    unittest.main()
