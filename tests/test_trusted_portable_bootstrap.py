import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from yuki_trust.portable_bootstrap import (
    BootstrapError,
    run_verified_bundle,
    stage_verified_bundle,
    verify_signed_bundle,
    _validate_manifest,
)


HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAS_CRYPTO, "cryptography is required for signed bootstrap tests")
class TrustedPortableBootstrapTests(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.runtime = self.bundle / "runtime"
        self.runtime.mkdir()
        self.python_executable = self.runtime / ("python.exe" if os.name == "nt" else "python")
        if os.name == "nt":
            self.python_executable.write_bytes(b"test-only interpreter placeholder")
        else:
            self.python_executable.write_text(
                "#!/bin/sh\nexec " + __import__("shlex").quote(sys.executable) + ' "$@"\n',
                encoding="utf-8",
            )
            self.python_executable.chmod(0o755)
        self.entrypoint = self.bundle / "portable_entry.py"
        self.entrypoint.write_text(
            "print('YUKI_BOOTSTRAP_OK')\n", encoding="utf-8"
        )
        (self.bundle / "payload.txt").write_text("verified payload", encoding="utf-8")

        self.private_key = Ed25519PrivateKey.generate()
        self.private_path = self.root / "release-private.pem"
        self.public_path = self.root / "release-public.pem"
        self.private_path.write_bytes(self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        self.public_path.write_bytes(self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        self.state_path = self.root / "host-state" / "release-state.json"
        self.state_path.parent.mkdir(mode=0o700)
        self._sign_bundle()

    def _sign_bundle(self, release_sequence=1, runtime_version="3.12.7"):
        files = []
        for path in sorted(p for p in self.bundle.rglob("*") if p.is_file() and p.name not in {"runtime-manifest.json", "runtime-manifest.sig"}):
            data = path.read_bytes()
            files.append({
                "path": path.relative_to(self.bundle).as_posix(),
                "size": len(data),
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
            })
        manifest = {
            "schema_version": 2,
            "product": "Yuki Portable Runtime",
            "artifact_kind": "portable-runtime",
            "platform": "test-platform",
            "runtime_version": runtime_version,
            "release_sequence": release_sequence,
            "files": files,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (self.bundle / "runtime-manifest.json").write_bytes(manifest_bytes)
        import base64
        (self.bundle / "runtime-manifest.sig").write_text(
            base64.b64encode(self.private_key.sign(manifest_bytes)).decode("ascii") + "\n",
            encoding="ascii",
        )

    def test_release_manifest_generator_output_is_accepted_by_host_bootstrap(self):
        from scripts.portable_runtime_manifest import (
            MANIFEST_NAME,
            create_manifest,
            sign_manifest,
        )

        manifest = create_manifest(
            self.bundle,
            platform="test-platform",
            runtime_version="3.12.7",
            release_sequence=1,
        )
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (self.bundle / MANIFEST_NAME).write_bytes(manifest_bytes)
        sign_manifest(self.bundle, self.private_path)

        verified = verify_signed_bundle(self.bundle, self.public_path)
        self.assertEqual(verified, manifest)

    def test_valid_bundle_authenticates(self):
        manifest = verify_signed_bundle(self.bundle, self.public_path)
        self.assertEqual(manifest["platform"], "test-platform")

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-relative inventory")
    def test_posix_inventory_does_not_use_path_walk(self):
        with patch("yuki_trust.portable_bootstrap.os.walk", side_effect=AssertionError("path walk must not be used")):
            manifest = verify_signed_bundle(self.bundle, self.public_path)
        self.assertEqual(
            {item["path"] for item in manifest["files"]},
            {"runtime/python", "portable_entry.py", "payload.txt"},
        )

    def test_rejects_bundle_for_different_host_platform(self):
        with self.assertRaisesRegex(BootstrapError, "Runtime platform mismatch"):
            verify_signed_bundle(
                self.bundle, self.public_path, expected_platform="windows-x86_64"
            )

    def test_accepts_bundle_for_exact_host_platform(self):
        manifest = verify_signed_bundle(
            self.bundle, self.public_path, expected_platform="test-platform"
        )
        self.assertEqual(manifest["platform"], "test-platform")

    def test_rejects_duplicate_json_manifest_keys_even_with_valid_signature(self):
        import base64

        manifest_path = self.bundle / "runtime-manifest.json"
        manifest_text = manifest_path.read_text(encoding="utf-8")
        self.assertIn('"schema_version": 2', manifest_text)
        ambiguous = manifest_text.replace(
            '"schema_version": 2',
            '"schema_version": 2,\n  "schema_version": 2',
            1,
        ).encode("utf-8")
        manifest_path.write_bytes(ambiguous)
        signature = self.private_key.sign(ambiguous)
        (self.bundle / "runtime-manifest.sig").write_text(
            base64.b64encode(signature).decode("ascii") + "\n",
            encoding="ascii",
        )
        with self.assertRaisesRegex(BootstrapError, "duplicate JSON object keys"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_rejects_case_colliding_directory_components_on_all_platforms(self):
        manifest = {
            "schema_version": 2,
            "product": "Yuki Portable Runtime",
            "artifact_kind": "portable-runtime",
            "platform": "test-platform",
            "runtime_version": "3.12.7",
            "release_sequence": 1,
            "files": [
                {"path": "Alpha/first.bin", "size": 1, "sha256": "0" * 64},
                {"path": "alpha/second.bin", "size": 1, "sha256": "0" * 64},
            ],
        }
        with self.assertRaisesRegex(BootstrapError, "colliding case-insensitive"):
            _validate_manifest(manifest)

    def test_rejects_file_directory_prefix_collision(self):
        manifest = {
            "schema_version": 2,
            "product": "Yuki Portable Runtime",
            "artifact_kind": "portable-runtime",
            "platform": "test-platform",
            "runtime_version": "3.12.7",
            "release_sequence": 1,
            "files": [
                {"path": "runtime", "size": 1, "sha256": "0" * 64},
                {"path": "runtime/python.exe", "size": 1, "sha256": "1" * 64},
            ],
        }
        with self.assertRaisesRegex(BootstrapError, "file/directory path collision"):
            _validate_manifest(manifest)

    @unittest.skipIf(os.name == "nt" or sys.platform == "darwin", "case-insensitive filesystems cannot represent this collision")
    def test_rejects_case_colliding_directory_components(self):
        (self.bundle / "Alpha").mkdir()
        (self.bundle / "alpha").mkdir()
        (self.bundle / "Alpha" / "first.bin").write_bytes(b"first")
        (self.bundle / "alpha" / "second.bin").write_bytes(b"second")
        self._sign_bundle()
        with self.assertRaisesRegex(BootstrapError, "colliding case-insensitive"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_rejects_tampered_payload(self):
        (self.bundle / "payload.txt").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(BootstrapError, "inventory or file hashes"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_rejects_wrong_trust_anchor(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        wrong = Ed25519PrivateKey.generate().public_key()
        wrong_path = self.root / "wrong-public.pem"
        wrong_path.write_bytes(wrong.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        with self.assertRaisesRegex(BootstrapError, "signature verification failed"):
            verify_signed_bundle(self.bundle, wrong_path)

    @unittest.skipIf(os.name == "nt", "POSIX no-follow open semantics")
    def test_rejects_symlinked_host_trust_anchor(self):
        link = self.root / "release-public-link.pem"
        try:
            link.symlink_to(self.public_path)
        except (OSError, NotImplementedError):
            self.skipTest("file symlinks unavailable")
        with self.assertRaisesRegex(BootstrapError, "Trusted public key"):
            verify_signed_bundle(self.bundle, link)

    def test_rejects_oversized_host_trust_anchor(self):
        oversized = self.root / "oversized-public.pem"
        oversized.write_bytes(b"x" * 65537)
        with self.assertRaisesRegex(BootstrapError, "Trusted public key"):
            verify_signed_bundle(self.bundle, oversized)

    def test_verification_uses_bounded_secure_host_file_reader(self):
        from yuki_trust.portable_host import _read_host_file_secure
        with patch("yuki_trust.portable_host._read_host_file_secure", wraps=_read_host_file_secure) as secure_read:
            verify_signed_bundle(self.bundle, self.public_path)
        secure_read.assert_called_once()
        self.assertEqual(secure_read.call_args.kwargs["max_bytes"], 65536)
        self.assertEqual(secure_read.call_args.kwargs["label"], "Host trust anchor")

    def test_rejects_trust_anchor_inside_bundle(self):
        in_bundle = self.bundle / "release-public.pem"
        in_bundle.write_bytes(self.public_path.read_bytes())
        with self.assertRaisesRegex(BootstrapError, "outside the removable bundle"):
            verify_signed_bundle(self.bundle, in_bundle)

    def test_staging_rejects_symlink_bundle_root_before_resolving(self):
        link = self.root / "staging-link"
        try:
            link.symlink_to(self.bundle, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable on this host")
        with self.assertRaisesRegex(BootstrapError, "must not be a symbolic link"):
            stage_verified_bundle(link, self.public_path)

    def test_rejects_symlink_bundle_root(self):
        link = self.root / "bundle-link"
        try:
            link.symlink_to(self.bundle, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks are unavailable on this host")
        with self.assertRaisesRegex(BootstrapError, "root must not be a symbolic link"):
            verify_signed_bundle(link, self.public_path)

    def test_rejects_missing_signature(self):
        (self.bundle / "runtime-manifest.sig").unlink()
        with self.assertRaisesRegex(BootstrapError, "missing"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_rejects_unexpected_file(self):
        (self.bundle / "extra.bin").write_bytes(b"unapproved")
        with self.assertRaisesRegex(BootstrapError, "inventory or file hashes"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_staging_revalidates_host_controlled_copy(self):
        with stage_verified_bundle(self.bundle, self.public_path) as temp_dir:
            staged = Path(temp_dir)
            self.assertTrue((staged / "payload.txt").is_file())
            self.assertEqual((staged / "payload.txt").read_text(encoding="utf-8"), "verified payload")
            verify_signed_bundle(staged, self.public_path)

    @unittest.skipIf(os.name == "nt", "shell executable smoke test is POSIX-specific")
    def test_launches_only_signed_entrypoint_from_staging(self):
        result = run_verified_bundle(
            self.bundle,
            self.public_path,
            "runtime/python",
            "portable_entry.py",
            anti_rollback_state_path=self.state_path,
        )
        self.assertEqual(result, 0)

    @unittest.skipUnless(os.name == "nt", "Windows interpreter invocation contract")
    def test_windows_launches_signed_interpreter_with_isolated_entrypoint(self):
        with patch("yuki_trust.portable_bootstrap.subprocess.run") as run:
            run.return_value.returncode = 0
            result = run_verified_bundle(
                self.bundle,
                self.public_path,
                "runtime/python.exe",
                "portable_entry.py",
                ["--diagnostic"],
                anti_rollback_state_path=self.state_path,
            )

        self.assertEqual(result, 0)
        run.assert_called_once()
        command = run.call_args.args[0]
        options = run.call_args.kwargs
        self.assertEqual(Path(command[0]).name, "python.exe")
        self.assertEqual(command[1], "-I")
        self.assertEqual(Path(command[2]).name, "portable_entry.py")
        self.assertEqual(command[3:], ["--diagnostic"])
        self.assertEqual(Path(options["cwd"]), Path(command[0]).parents[1])
        self.assertFalse(Path(command[0]).is_relative_to(self.bundle))

    def test_rejects_backslash_paths_before_staging(self):
        for interpreter, entrypoint in (
            ("runtime\\python.exe", "portable_entry.py"),
            ("runtime/python", "folder\\portable_entry.py"),
        ):
            with self.subTest(interpreter=interpreter, entrypoint=entrypoint):
                with self.assertRaisesRegex(BootstrapError, "canonical relative path"):
                    run_verified_bundle(
                        self.bundle,
                        self.public_path,
                        interpreter,
                        entrypoint,
                        anti_rollback_state_path=self.state_path,
                    )

    def test_rejects_unsigned_or_unlisted_entrypoint(self):
        with self.assertRaisesRegex(BootstrapError, "not part of the signed"):
            run_verified_bundle(
                self.bundle,
                self.public_path,
                "runtime/python",
                "bin/not-listed",
                anti_rollback_state_path=self.state_path,
            )

    def test_launcher_rejects_older_signed_release_after_newer_release(self):
        self._sign_bundle(release_sequence=12)
        with patch("yuki_trust.portable_bootstrap.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertEqual(run_verified_bundle(self.bundle, self.public_path, self.python_executable.relative_to(self.bundle).as_posix(), "portable_entry.py", anti_rollback_state_path=self.state_path), 0)
            self._sign_bundle(release_sequence=11)
            with self.assertRaisesRegex(BootstrapError, "rollback rejected"):
                run_verified_bundle(self.bundle, self.public_path, self.python_executable.relative_to(self.bundle).as_posix(), "portable_entry.py", anti_rollback_state_path=self.state_path)
            run.assert_called_once()

    def test_launcher_rejects_same_sequence_with_different_signed_manifest(self):
        self._sign_bundle(release_sequence=12, runtime_version="3.12.7")
        with patch("yuki_trust.portable_bootstrap.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertEqual(run_verified_bundle(self.bundle, self.public_path, self.python_executable.relative_to(self.bundle).as_posix(), "portable_entry.py", anti_rollback_state_path=self.state_path), 0)
            self._sign_bundle(release_sequence=12, runtime_version="3.12.8")
            with self.assertRaisesRegex(BootstrapError, "equivocation"):
                run_verified_bundle(self.bundle, self.public_path, self.python_executable.relative_to(self.bundle).as_posix(), "portable_entry.py", anti_rollback_state_path=self.state_path)
            run.assert_called_once()

    def test_rejects_anti_rollback_state_inside_removable_bundle(self):
        state = self.bundle / 'release-state.json'
        with self.assertRaisesRegex(BootstrapError, 'outside the removable bundle'):
            run_verified_bundle(self.bundle, self.public_path, 'runtime/python', 'portable_entry.py', anti_rollback_state_path=state)

    def test_rejects_non_integer_release_sequence(self):
        manifest = {
            'schema_version': 1, 'product': 'Yuki Portable Runtime',
            'artifact_kind': 'portable-runtime', 'platform': 'test-platform',
            'runtime_version': '3.12.7', 'release_sequence': True,
            'files': [{'path': 'x', 'size': 1, 'sha256': '0' * 64}],
        }
        with self.assertRaisesRegex(BootstrapError, 'metadata is invalid'):
            _validate_manifest(manifest)

if __name__ == "__main__":
    unittest.main()
