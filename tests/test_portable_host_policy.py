import hashlib
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
        self.state_path = self.trust_dir / "release-state.json"
        self.write_policy()

    def write_policy(self, **overrides):
        value = {
            "schema_version": 1,
            "public_key": str(self.public_key),
            "expected_platform": "linux-x86_64",
            "python": "runtime/bin/python3",
            "entrypoint": "portable_entry.py",
            "anti_rollback_state": str(self.state_path),
        }
        value.update(overrides)
        self.policy.write_text(json.dumps(value), encoding="utf-8")
        if os.name != "nt":
            self.policy.chmod(0o600)
            self.public_key.chmod(0o600)

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-relative open semantics")
    def test_secure_policy_reader_uses_descriptor_relative_file_access(self):
        from yuki_trust.portable_host import _read_host_file_secure

        content = _read_host_file_secure(
            self.policy, label="Test policy", max_bytes=64 * 1024
        )
        self.assertEqual(json.loads(content.decode("utf-8"))["schema_version"], 1)

    @unittest.skipIf(os.name == "nt", "POSIX no-follow open semantics")
    def test_secure_policy_reader_rejects_symlinked_file_and_parent(self):
        from yuki_trust.portable_host import _read_host_file_secure

        file_link = self.trust_dir / "policy-file-link.json"
        parent_link = self.root / "host-trust-link"
        try:
            file_link.symlink_to(self.policy)
            parent_link.symlink_to(self.trust_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        for candidate in (file_link, parent_link / self.policy.name):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(BootstrapError, "could not be securely read"):
                    _read_host_file_secure(
                        candidate, label="Test policy", max_bytes=64 * 1024
                    )

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-relative open semantics")
    def test_secure_policy_reader_enforces_size_limit(self):
        from yuki_trust.portable_host import _read_host_file_secure

        with self.assertRaisesRegex(BootstrapError, "exceeds its size limit"):
            _read_host_file_secure(self.policy, label="Test policy", max_bytes=4)

    @unittest.skipUnless(os.name == "posix" and Path("/proc/self/fd").is_dir(), "Linux /proc file descriptor accounting")
    def test_posix_secure_reader_closes_descriptors_after_repeated_failures(self):
        from yuki_trust.portable_host import _read_host_file_secure

        def descriptor_count():
            return len(os.listdir("/proc/self/fd"))

        before = descriptor_count()
        for _ in range(10):
            with self.assertRaisesRegex(BootstrapError, "exceeds its size limit"):
                _read_host_file_secure(
                    self.policy, label="Test policy", max_bytes=1
                )
            with self.assertRaisesRegex(BootstrapError, "could not be securely read"):
                _read_host_file_secure(
                    self.trust_dir / "missing-policy.json",
                    label="Test policy",
                    max_bytes=64 * 1024,
                )
        after = descriptor_count()
        self.assertLessEqual(
            after, before + 1,
            "rejected POSIX secure reads leaked file descriptors",
        )

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-relative read semantics")
    def test_posix_secure_reader_rejects_in_place_mutation_during_read(self):
        import os as os_module

        from yuki_trust.portable_host import _read_host_file_secure

        original = os_module.read
        mutated = False

        def mutate_after_first_read(fd, size):
            nonlocal mutated
            chunk = original(fd, size)
            if chunk and not mutated:
                mutated = True
                self.policy.write_bytes(b"X" * self.policy.stat().st_size)
            return chunk

        with patch("os.read", side_effect=mutate_after_first_read):
            with self.assertRaisesRegex(BootstrapError, "changed while being read"):
                _read_host_file_secure(
                    self.policy, label="Test policy", max_bytes=64 * 1024
                )
        self.assertTrue(mutated, "test did not mutate the file during the read")

    @unittest.skipUnless(os.name == "nt", "Windows native handle read semantics")
    def test_windows_secure_reader_reads_regular_file_and_enforces_size_limit(self):
        from yuki_trust.portable_host import _read_host_file_secure

        self.assertEqual(
            _read_host_file_secure(self.policy, label="Test policy", max_bytes=64 * 1024),
            self.policy.read_bytes(),
        )
        with self.assertRaisesRegex(BootstrapError, "exceeds its size limit"):
            _read_host_file_secure(self.policy, label="Test policy", max_bytes=1)

    @unittest.skipUnless(os.name == "nt", "Windows native handle sharing semantics")
    def test_windows_secure_reader_blocks_concurrent_writer_handles(self):
        import ctypes
        from ctypes import wintypes

        from yuki_trust.portable_host import _read_host_file_secure

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        invalid = wintypes.HANDLE(-1).value
        original_win_dll = ctypes.WinDLL
        writer_denied = False

        class _KernelProxy:
            def __init__(self, library):
                self._library = library

            def __getattr__(self, name):
                return getattr(self._library, name)

            def CreateFileW(self, path, access, share, security, creation, flags, template):
                nonlocal writer_denied
                handle = self._library.CreateFileW(
                    path, access, share, security, creation, flags, template
                )
                if handle != invalid and not writer_denied:
                    writer = create_file(
                        str(self_policy), 0x40000000, 0x00000007, None, 3,
                        0x08000000, None,
                    )
                    if writer != invalid:
                        close_handle(writer)
                        raise AssertionError("writer unexpectedly opened while secure reader held the file")
                    writer_denied = ctypes.get_last_error() == 32
                return handle

        self_policy = self.policy

        def win_dll(name, *args, **kwargs):
            library = original_win_dll(name, *args, **kwargs)
            if name.lower() == "kernel32":
                return _KernelProxy(library)
            return library

        expected = self.policy.read_bytes()
        with patch("ctypes.WinDLL", side_effect=win_dll):
            actual = _read_host_file_secure(
                self.policy, label="Test policy", max_bytes=64 * 1024
            )
        self.assertEqual(actual, expected)
        self.assertTrue(
            writer_denied,
            "secure reader did not block a concurrent write handle",
        )

    @unittest.skipUnless(os.name == "nt", "Windows native handle sharing semantics")
    def test_windows_secure_reader_rejects_preexisting_writer_handle(self):
        import ctypes
        from ctypes import wintypes

        from yuki_trust.portable_host import _read_host_file_secure

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        invalid = wintypes.HANDLE(-1).value
        writer = create_file(
            str(self.policy), 0x40000000, 0x00000007, None, 3, 0x08000000, None
        )
        if writer == invalid:
            self.skipTest("could not open a pre-existing writer handle")
        try:
            # Windows sharing compatibility is bidirectional: the secure
            # reader's FILE_SHARE_READ-only request cannot coexist with an
            # already-open handle that has write access.
            with self.assertRaisesRegex(
                BootstrapError, "could not be securely read"
            ):
                _read_host_file_secure(
                    self.policy, label="Test policy", max_bytes=64 * 1024
                )
        finally:
            close_handle(writer)

    @unittest.skipUnless(os.name == "nt", "Windows native handle semantics")
    def test_windows_secure_reader_rejects_directory_handles(self):
        from yuki_trust.portable_host import _read_host_file_secure

        with self.assertRaisesRegex(BootstrapError, "directory|could not be securely read"):
            _read_host_file_secure(
                self.trust_dir, label="Test policy", max_bytes=64 * 1024
            )

    @unittest.skipUnless(os.name == "nt", "Windows native handle cleanup")
    def test_windows_secure_reader_closes_handles_after_repeated_failures(self):
        import ctypes
        from yuki_trust.portable_host import _read_host_file_secure

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_handle_count = kernel32.GetProcessHandleCount
        get_handle_count.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        get_handle_count.restype = ctypes.c_int
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = ctypes.c_void_p

        def handle_count():
            count = ctypes.c_ulong()
            self.assertTrue(get_handle_count(get_current_process(), ctypes.byref(count)))
            return count.value

        before = handle_count()
        for _ in range(10):
            with self.assertRaisesRegex(BootstrapError, "exceeds its size limit"):
                _read_host_file_secure(
                    self.policy, label="Test policy", max_bytes=1
                )
            with self.assertRaisesRegex(BootstrapError, "directory|could not be securely read"):
                _read_host_file_secure(
                    self.trust_dir, label="Test policy", max_bytes=64 * 1024
                )
        after = handle_count()
        self.assertLessEqual(
            after, before + 1,
            "rejected Windows secure reads leaked native file handles",
        )

    @unittest.skipUnless(os.name == "nt", "Windows native handle copy semantics")
    def test_windows_secure_copy_hashes_source_and_creates_target_exclusively(self):
        from yuki_trust.portable_host import _copy_windows_file_handle_secure

        source = self.trust_dir / "source.bin"
        target = self.bundle / "copied.bin"
        payload = b"authenticated portable payload"
        source.write_bytes(payload)
        _copy_windows_file_handle_secure(
            source,
            target,
            label="Test payload",
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
        self.assertEqual(target.read_bytes(), payload)
        with self.assertRaisesRegex(BootstrapError, "securely staged"):
            _copy_windows_file_handle_secure(
                source,
                target,
                label="Test payload",
                expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )

    @unittest.skipUnless(os.name == "nt", "Windows native handle copy semantics")
    def test_windows_secure_copy_rejects_source_symlink(self):
        from yuki_trust.portable_host import _copy_windows_file_handle_secure

        source = self.trust_dir / "source-link.bin"
        target = self.bundle / "copied.bin"
        try:
            source.symlink_to(self.public_key)
        except (OSError, NotImplementedError):
            self.skipTest("Windows file symlink creation is unavailable")
        with self.assertRaisesRegex(BootstrapError, "reparse point"):
            _copy_windows_file_handle_secure(
                source, target, label="Test payload", max_bytes=1024
            )

    @unittest.skipUnless(os.name == "nt", "Windows reparse-point semantics")
    def test_windows_secure_reader_rejects_file_symlink(self):
        from yuki_trust.portable_host import _read_host_file_secure

        link = self.trust_dir / "policy-link.json"
        try:
            link.symlink_to(self.policy)
        except (OSError, NotImplementedError):
            self.skipTest("Windows file symlink creation is unavailable")
        with self.assertRaisesRegex(BootstrapError, "reparse point"):
            _read_host_file_secure(link, label="Test policy", max_bytes=64 * 1024)

    def test_loads_host_owned_policy_and_pins_launch_parameters(self):
        policy = load_host_policy(self.policy, self.bundle)
        self.assertEqual(policy["public_key"], str(self.public_key.resolve()))
        self.assertEqual(policy["expected_platform"], "linux-x86_64")
        self.assertEqual(policy["python"], "runtime/bin/python3")
        self.assertEqual(policy["entrypoint"], "portable_entry.py")
        self.assertEqual(policy["anti_rollback_state"], str(self.state_path.parent.resolve() / self.state_path.name))

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
            anti_rollback_state_path=str(self.state_path.resolve()),
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
