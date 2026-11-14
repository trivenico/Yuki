import hashlib
import json
import os
import sys
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
        fingerprint = hashlib.sha256(b"validated-ed25519-key").hexdigest()
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
            patch.object(installer, "_apply_windows_acl"),
        ):
            return installer.install_host_trust(self.key, self.install, self.state, fingerprint)

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
                installer.install_host_trust(self.key, self.install, self.state, hashlib.sha256(b"validated-ed25519-key").hexdigest())
        self.assertEqual((self.install / installer._KEY_NAME).read_text(encoding="ascii"), "preexisting")

    def test_refuses_same_or_nested_trust_and_state_roots(self):
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
        ):
            with self.assertRaisesRegex(BootstrapError, "must be separate"):
                installer.install_host_trust(self.key, self.install, self.install, hashlib.sha256(b"validated-ed25519-key").hexdigest())
            with self.assertRaisesRegex(BootstrapError, "must be separate"):
                installer.install_host_trust(self.key, self.install, self.install / "state", hashlib.sha256(b"validated-ed25519-key").hexdigest())

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_rejects_symlinked_install_root(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "linked"
        link.symlink_to(real, target_is_directory=True)
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
        ):
            with self.assertRaisesRegex(BootstrapError, "real directory, not a symlink"):
                installer.install_host_trust(self.key, link, self.state, hashlib.sha256(b"validated-ed25519-key").hexdigest())

    def test_windows_path_chain_rejects_reparse_point_ancestor(self):
        target = self.root.resolve()
        checked_path = self.install.resolve()
        original_lstat = Path.lstat

        def fake_lstat(path):
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x0400)
            return original_lstat(path)

        with patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
            with self.assertRaisesRegex(BootstrapError, "junctions, or reparse points"):
                installer._validate_windows_path_chain(checked_path, "Host install")

    def test_windows_path_chain_accepts_normal_existing_ancestors(self):
        installer._validate_windows_path_chain((self.root / "trusted").resolve(), "Host install")

    def test_rejects_mismatched_out_of_band_key_fingerprint(self):
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
        ):
            with self.assertRaisesRegex(BootstrapError, "does not match"):
                installer.install_host_trust(self.key, self.install, self.state, "0" * 64)

    def test_rejects_malformed_out_of_band_key_fingerprint(self):
        with (
            patch.object(portable_host, "__file__", str(Path(sys.prefix) / "lib/yuki_trust/portable_host.py")),
            patch.object(installer, "_validate_public_key", return_value=b"validated-ed25519-key"),
        ):
            with self.assertRaisesRegex(BootstrapError, "64 hexadecimal"):
                installer.install_host_trust(self.key, self.install, self.state, "not-a-fingerprint")

    @unittest.skipIf(os.name == "nt", "POSIX launcher wrapper")
    def test_posix_launcher_quotes_trusted_interpreter_and_preserves_arguments(self):
        result = self._install()
        launcher = Path(result["launcher"])
        source = launcher.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("#!/bin/sh\n"))
        self.assertIn("exec ", source)
        self.assertIn('"$@"', source)
        self.assertTrue(source.endswith("\n"))
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertTrue(Path(result["public_key"]).is_file())

    @unittest.skipUnless(os.name == "nt", "requires real Windows filesystem ACLs")
    def test_windows_acl_is_enforced_on_real_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            installer._apply_windows_acl(target)

            identity = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout
            user_sid = __import__("csv").reader(identity.splitlines())
            user_sid = list(user_sid)[-1][-1].strip()
            script = r"""
$acl = Get-Acl -LiteralPath $env:YUKI_ACL_PATH
if (-not $acl.AreAccessRulesProtected) { exit 10 }
$expected = @($env:YUKI_USER_SID, 'S-1-5-18', 'S-1-5-32-544')
$required = [System.Security.AccessControl.InheritanceFlags]::ObjectInherit -bor [System.Security.AccessControl.InheritanceFlags]::ContainerInherit
foreach ($sid in $expected) {
    $found = $false
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { continue }
        try { $actual = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { continue }
        if ($actual -eq $sid -and
            ($rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq [System.Security.AccessControl.FileSystemRights]::FullControl -and
            ($rule.InheritanceFlags -band $required) -eq $required) {
            $found = $true
            break
        }
    }
    if (-not $found) { exit 11 }
}
exit 0
"""
            environment = dict(os.environ)
            environment["YUKI_ACL_PATH"] = str(target)
            environment["YUKI_USER_SID"] = user_sid
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                check=True, capture_output=True, text=True, timeout=30, env=environment,
            )

    def test_windows_acl_grants_directory_and_inherited_full_control(self):
        completed = type("Completed", (), {"stdout": "User Name,SID\r\nDOMAIN\\user,S-1-5-21-1-2-3-1001\r\n"})()
        with (
            patch.object(installer.os, "name", "nt"),
            patch.object(installer.subprocess, "run", return_value=completed) as run,
        ):
            installer._apply_windows_acl(self.install)
        commands = [call.args[0] for call in run.call_args_list]
        acl_command = commands[1]
        self.assertIn("*S-1-5-21-1-2-3-1001:(OI)(CI)F", acl_command)
        self.assertIn("*S-1-5-18:(OI)(CI)F", acl_command)
        self.assertIn("*S-1-5-32-544:(OI)(CI)F", acl_command)


if __name__ == "__main__":
    unittest.main()
