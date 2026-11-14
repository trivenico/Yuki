import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.portable_runtime_manifest import (
    MANIFEST_NAME,
    RuntimeManifestError,
    create_manifest,
    verify_manifest,
)


class PortableRuntimeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runtime"
        (self.root / "python" / "bin").mkdir(parents=True)
        (self.root / "python" / "bin" / "python").write_bytes(b"runtime-binary")
        (self.root / "yuki").mkdir()
        (self.root / "yuki" / "app.py").write_text("print('Yuki')\n", encoding="utf-8")

    def _write_manifest(self):
        manifest = create_manifest(self.root, platform="linux-x86_64", runtime_version="3.12.7")
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_create_and_verify_manifest(self):
        manifest = self._write_manifest()
        verified = verify_manifest(self.root)
        self.assertEqual(verified, manifest)
        self.assertEqual(manifest["artifact_kind"], "portable-runtime")
        self.assertEqual(len(manifest["files"]), 2)

    def test_modified_file_fails_integrity_check(self):
        self._write_manifest()
        target = self.root / "yuki" / "app.py"
        target.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "hashes"):
            verify_manifest(self.root)

    def test_added_file_fails_exact_inventory_check(self):
        self._write_manifest()
        (self.root / "unexpected.dll").write_bytes(b"extra")
        with self.assertRaisesRegex(RuntimeManifestError, "inventory"):
            verify_manifest(self.root)

    def test_manifest_path_traversal_is_rejected(self):
        manifest = self._write_manifest()
        manifest["files"][0]["path"] = "../escape"
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "escapes"):
            verify_manifest(self.root)

    def test_duplicate_manifest_paths_are_rejected(self):
        manifest = self._write_manifest()
        manifest["files"][1]["path"] = manifest["files"][0]["path"]
        manifest["files"].sort(key=lambda item: item["path"])
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "duplicate"):
            verify_manifest(self.root)

    def test_manifest_rejects_windows_drive_and_reserved_names(self):
        manifest = self._write_manifest()
        manifest["files"][0]["path"] = "C:/outside.dll"
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "not portable to Windows"):
            verify_manifest(self.root)

        manifest["files"][0]["path"] = "payload/CON.txt"
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "not portable to Windows"):
            verify_manifest(self.root)

    def test_manifest_rejects_case_insensitive_path_collisions(self):
        manifest = self._write_manifest()
        manifest["files"][0]["path"] = "payload/Model.bin"
        manifest["files"][1]["path"] = "payload/model.bin"
        manifest["files"].sort(key=lambda item: item["path"])
        (self.root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeManifestError, "case-insensitive"):
            verify_manifest(self.root)

    def test_symlink_payload_is_rejected(self):
        self._write_manifest()
        link = self.root / "python" / "linked-python"
        try:
            link.symlink_to(self.root / "python" / "bin" / "python")
        except OSError:
            self.skipTest("symlink creation is not permitted")
        with self.assertRaisesRegex(RuntimeManifestError, "symbolic-link"):
            verify_manifest(self.root)

    def test_create_cli_rejects_symlink_manifest_destination(self):
        import sys
        from unittest.mock import patch
        from scripts.portable_runtime_manifest import main

        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            link = self.root / MANIFEST_NAME
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            with patch.object(sys, "argv", [
                "portable_runtime_manifest.py", "create", str(self.root),
                "--platform", "linux-x86_64", "--runtime-version", "3.12.7",
            ]):
                self.assertEqual(main(), 2)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_signed_release_verifies_with_out_of_bundle_trust_anchor(self):
        import importlib.util
        if not importlib.util.find_spec("cryptography"):
            self.skipTest("cryptography optional dependency is not installed")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from scripts.portable_runtime_manifest import sign_manifest, verify_signed_manifest

        self._write_manifest()
        private_key = Ed25519PrivateKey.generate()
        private_path = Path(self.temp.name) / "release-private.pem"
        public_path = Path(self.temp.name) / "release-public.pem"
        private_path.write_bytes(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        public_path.write_bytes(private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        sign_manifest(self.root, private_path)
        verified = verify_signed_manifest(self.root, public_path)
        self.assertEqual(verified["platform"], "linux-x86_64")
        self.assertTrue((self.root / "runtime-manifest.sig").is_file())

    def test_signed_release_rejects_wrong_trust_anchor(self):
        import importlib.util
        if not importlib.util.find_spec("cryptography"):
            self.skipTest("cryptography optional dependency is not installed")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from scripts.portable_runtime_manifest import sign_manifest, verify_signed_manifest

        self._write_manifest()
        trusted = Ed25519PrivateKey.generate()
        attacker = Ed25519PrivateKey.generate()
        private_path = Path(self.temp.name) / "release-private.pem"
        wrong_public_path = Path(self.temp.name) / "wrong-public.pem"
        private_path.write_bytes(trusted.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        wrong_public_path.write_bytes(attacker.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        sign_manifest(self.root, private_path)
        with self.assertRaisesRegex(RuntimeManifestError, "signature verification failed"):
            verify_signed_manifest(self.root, wrong_public_path)

    def test_signed_release_rejects_tampered_signature(self):
        import importlib.util
        if not importlib.util.find_spec("cryptography"):
            self.skipTest("cryptography optional dependency is not installed")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from scripts.portable_runtime_manifest import sign_manifest, verify_signed_manifest

        self._write_manifest()
        private_key = Ed25519PrivateKey.generate()
        private_path = Path(self.temp.name) / "release-private.pem"
        public_path = Path(self.temp.name) / "release-public.pem"
        private_path.write_bytes(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        public_path.write_bytes(private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        sign_manifest(self.root, private_path)
        signature_path = self.root / "runtime-manifest.sig"
        signature_path.write_text("A" * 88, encoding="ascii")
        with self.assertRaisesRegex(RuntimeManifestError, "signature verification failed"):
            verify_signed_manifest(self.root, public_path)

    def test_signed_release_rejects_payload_tampering_after_signing(self):
        import importlib.util
        if not importlib.util.find_spec("cryptography"):
            self.skipTest("cryptography optional dependency is not installed")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from scripts.portable_runtime_manifest import sign_manifest, verify_signed_manifest

        self._write_manifest()
        private_key = Ed25519PrivateKey.generate()
        private_path = Path(self.temp.name) / "release-private.pem"
        public_path = Path(self.temp.name) / "release-public.pem"
        private_path.write_bytes(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        public_path.write_bytes(private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ))
        sign_manifest(self.root, private_path)
        (self.root / "python" / "bin" / "python").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeManifestError, "inventory or file hashes"):
            verify_signed_manifest(self.root, public_path)

    def test_signing_key_must_be_outside_portable_bundle(self):
        import importlib.util
        if not importlib.util.find_spec("cryptography"):
            self.skipTest("cryptography optional dependency is not installed")
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from scripts.portable_runtime_manifest import sign_manifest

        self._write_manifest()
        private_key = Ed25519PrivateKey.generate()
        private_path = self.root / "private.pem"
        private_path.write_bytes(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        with self.assertRaisesRegex(RuntimeManifestError, "outside"):
            sign_manifest(self.root, private_path)

    def test_unsigned_manifest_does_not_claim_authenticity(self):
        manifest = self._write_manifest()
        self.assertEqual(manifest["artifact_kind"], "portable-runtime")
        self.assertNotIn("signature", manifest)


if __name__ == "__main__":
    unittest.main()
