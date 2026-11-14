import csv
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

            # Verify SE_DACL_PROTECTED through Win32 APIs. This avoids relying on
            # PowerShell modules, which are not consistently available on hosted runners.
            import ctypes
            from ctypes import wintypes

            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            security_descriptor = ctypes.c_void_p()
            owner = ctypes.c_void_p()
            group = ctypes.c_void_p()
            dacl = ctypes.c_void_p()
            get_named_security_info = advapi32.GetNamedSecurityInfoW
            get_named_security_info.argtypes = [
                wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            get_named_security_info.restype = wintypes.DWORD
            get_control = advapi32.GetSecurityDescriptorControl
            get_control.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
                ctypes.POINTER(wintypes.DWORD),
            ]
            get_control.restype = wintypes.BOOL
            local_free = kernel32.LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            try:
                status = get_named_security_info(
                    str(target), 1, 0x4, ctypes.byref(owner), ctypes.byref(group),
                    ctypes.byref(dacl), None, ctypes.byref(security_descriptor),
                )
                self.assertEqual(status, 0, f"GetNamedSecurityInfoW failed: {status}")
                control = wintypes.WORD()
                revision = wintypes.DWORD()
                self.assertTrue(
                    get_control(security_descriptor, ctypes.byref(control), ctypes.byref(revision)),
                    f"GetSecurityDescriptorControl failed: {ctypes.get_last_error()}",
                )
                self.assertTrue(
                    control.value & 0x1000,
                    f"DACL is not protected (control=0x{control.value:04x})",
                )
                self.assertIsNotNone(dacl.value, "Windows directory has no DACL")
                # Inspect the real ACL entries, not just the protected-DACL flag.
                class ACL_SIZE_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("AceCount", wintypes.DWORD),
                        ("AclBytesInUse", wintypes.DWORD),
                        ("AclBytesFree", wintypes.DWORD),
                    ]

                get_acl_information = advapi32.GetAclInformation
                get_acl_information.argtypes = [
                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
                ]
                get_acl_information.restype = wintypes.BOOL
                get_ace = advapi32.GetAce
                get_ace.argtypes = [
                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                ]
                get_ace.restype = wintypes.BOOL
                convert_sid = advapi32.ConvertSidToStringSidW
                convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
                convert_sid.restype = wintypes.BOOL

                acl_info = ACL_SIZE_INFORMATION()
                self.assertTrue(
                    get_acl_information(
                        dacl, ctypes.byref(acl_info), ctypes.sizeof(acl_info), 2,
                    ),
                    f"GetAclInformation failed: {ctypes.get_last_error()}",
                )
                allowed = {}
                for ace_index in range(acl_info.AceCount):
                    ace = ctypes.c_void_p()
                    self.assertTrue(
                        get_ace(dacl, ace_index, ctypes.byref(ace)),
                        f"GetAce({ace_index}) failed: {ctypes.get_last_error()}",
                    )
                    ace_type, ace_flags = ctypes.cast(
                        ace, ctypes.POINTER(ctypes.c_ubyte * 2),
                    ).contents
                    if ace_type != 0:  # ACCESS_ALLOWED_ACE_TYPE
                        continue
                    raw = ctypes.string_at(ace, 8)
                    access_mask = int.from_bytes(raw[4:8], "little")
                    sid_text = wintypes.LPWSTR()
                    sid_pointer = ctypes.c_void_p(ace.value + 8)
                    self.assertTrue(
                        convert_sid(sid_pointer, ctypes.byref(sid_text)),
                        f"ConvertSidToStringSidW failed: {ctypes.get_last_error()}",
                    )
                    try:
                        allowed[sid_text.value] = (ace_flags, access_mask)
                    finally:
                        local_free(ctypes.cast(sid_text, ctypes.c_void_p))

                expected_sids = (
                    "S-1-5-18",       # Local System
                    "S-1-5-32-544",   # Built-in Administrators
                )
                whoami = subprocess.run(
                    ["whoami", "/user", "/fo", "csv", "/nh"],
                    check=True, capture_output=True, text=True, timeout=10,
                ).stdout
                identity_rows = list(csv.reader(whoami.splitlines()))
                self.assertTrue(identity_rows and identity_rows[-1])
                expected_sids += (identity_rows[-1][-1].strip(),)
                for sid in expected_sids:
                    self.assertIn(sid, allowed, f"Missing full-control ACE for {sid}")
                    ace_flags, access_mask = allowed[sid]
                    self.assertEqual(ace_flags & 0x03, 0x03, f"ACE for {sid} lacks OI/CI inheritance")
                    self.assertTrue(
                        access_mask & 0x10000000 or access_mask & 0x1F01FF == 0x1F01FF,
                        f"ACE for {sid} does not grant full control (mask=0x{access_mask:08x})",
                    )

            finally:
                if security_descriptor:
                    local_free(security_descriptor)

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


    @unittest.skipUnless(os.name == "nt", "requires real Windows filesystem ACLs")
    def test_actual_install_provisions_inherited_acl_on_trust_files(self):
        # Exercise the production installer without mocking its ACL operation.
        # Only key parsing and the installed-package location are isolated.
        fingerprint = hashlib.sha256(b"validated-ed25519-key").hexdigest()
        with (
            patch.object(
                portable_host, "__file__",
                str(Path(sys.prefix) / "Lib/site-packages/yuki_trust/portable_host.py"),
            ),
            patch.object(
                installer, "_validate_public_key",
                return_value=b"validated-ed25519-key",
            ),
        ):
            result = installer.install_host_trust(
                self.key, self.install, self.state, fingerprint,
            )

        expected_identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout
        identity_rows = list(csv.reader(expected_identity.splitlines()))
        self.assertTrue(identity_rows and identity_rows[-1])
        expected_sids = {
            "S-1-5-18", "S-1-5-32-544",
            identity_rows[-1][-1].strip(),
        }

        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class ACL_SIZE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("AceCount", wintypes.DWORD),
                ("AclBytesInUse", wintypes.DWORD),
                ("AclBytesFree", wintypes.DWORD),
            ]

        get_security = advapi32.GetNamedSecurityInfoW
        get_security.argtypes = [
            wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_security.restype = wintypes.DWORD
        get_acl_info = advapi32.GetAclInformation
        get_acl_info.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
        ]
        get_acl_info.restype = wintypes.BOOL
        get_ace = advapi32.GetAce
        get_ace.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ]
        get_ace.restype = wintypes.BOOL
        convert_sid = advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        convert_sid.restype = wintypes.BOOL
        local_free = kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p

        provisioned_files = (
            Path(result["public_key"]),
            Path(result["policy"]),
            Path(result["launcher"]),
        )
        for file_path in provisioned_files:
            self.assertTrue(file_path.is_file(), f"Missing provisioned file: {file_path}")
            owner = ctypes.c_void_p()
            group = ctypes.c_void_p()
            dacl = ctypes.c_void_p()
            descriptor = ctypes.c_void_p()
            status = get_security(
                str(file_path), 1, 0x4,
                ctypes.byref(owner), ctypes.byref(group), ctypes.byref(dacl),
                None, ctypes.byref(descriptor),
            )
            self.assertEqual(status, 0, f"GetNamedSecurityInfoW failed for {file_path}: {status}")
            try:
                self.assertIsNotNone(dacl.value, f"Missing DACL on {file_path}")
                info = ACL_SIZE_INFORMATION()
                self.assertTrue(
                    get_acl_info(dacl, ctypes.byref(info), ctypes.sizeof(info), 2),
                    f"GetAclInformation failed for {file_path}: {ctypes.get_last_error()}",
                )
                inherited = {}
                for index in range(info.AceCount):
                    ace = ctypes.c_void_p()
                    self.assertTrue(
                        get_ace(dacl, index, ctypes.byref(ace)),
                        f"GetAce({index}) failed for {file_path}: {ctypes.get_last_error()}",
                    )
                    header = ctypes.string_at(ace, 8)
                    if header[0] != 0:  # ACCESS_ALLOWED_ACE_TYPE
                        continue
                    flags = header[1]
                    mask = int.from_bytes(header[4:8], "little")
                    sid_text = wintypes.LPWSTR()
                    self.assertTrue(
                        convert_sid(ctypes.c_void_p(ace.value + 8), ctypes.byref(sid_text)),
                        f"ConvertSidToStringSidW failed: {ctypes.get_last_error()}",
                    )
                    try:
                        inherited[sid_text.value] = (flags, mask)
                    finally:
                        local_free(ctypes.cast(sid_text, ctypes.c_void_p))

                for sid in expected_sids:
                    self.assertIn(
                        sid, inherited,
                        f"{file_path} did not inherit an allow ACE for {sid}",
                    )
                    flags, mask = inherited[sid]
                    self.assertTrue(
                        flags & 0x10,
                        f"ACE for {sid} on {file_path} is not inherited (flags=0x{flags:02x})",
                    )
                    self.assertTrue(
                        mask & 0x10000000 or mask & 0x1F01FF == 0x1F01FF,
                        f"ACE for {sid} on {file_path} lacks full control (mask=0x{mask:08x})",
                    )
            finally:
                if descriptor:
                    local_free(descriptor)


if __name__ == "__main__":
    unittest.main()
