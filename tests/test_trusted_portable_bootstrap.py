import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from yuki_trust.portable_bootstrap import (
    BootstrapError,
    run_verified_bundle,
    stage_verified_bundle,
    verify_signed_bundle,
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
        self.payload = self.bundle / "bin"
        self.payload.mkdir()
        self.entrypoint = self.payload / ("yuki.cmd" if os.name == "nt" else "yuki")
        if os.name == "nt":
            self.entrypoint.write_text("@echo off\r\necho YUKI_BOOTSTRAP_OK\r\n", encoding="utf-8")
        else:
            self.entrypoint.write_text("#!/bin/sh\nprintf 'YUKI_BOOTSTRAP_OK\\n'\n", encoding="utf-8")
            self.entrypoint.chmod(0o755)
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
        self._sign_bundle()

    def _sign_bundle(self):
        files = []
        for path in sorted(p for p in self.bundle.rglob("*") if p.is_file() and p.name not in {"runtime-manifest.json", "runtime-manifest.sig"}):
            data = path.read_bytes()
            files.append({
                "path": path.relative_to(self.bundle).as_posix(),
                "size": len(data),
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
            })
        manifest = {
            "schema_version": 1,
            "product": "Yuki Portable Runtime",
            "artifact_kind": "unsigned-portable-runtime",
            "platform": "test-platform",
            "runtime_version": "3.12.7",
            "files": files,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (self.bundle / "runtime-manifest.json").write_bytes(manifest_bytes)
        import base64
        (self.bundle / "runtime-manifest.sig").write_text(
            base64.b64encode(self.private_key.sign(manifest_bytes)).decode("ascii") + "\n",
            encoding="ascii",
        )

    def test_valid_bundle_authenticates(self):
        manifest = verify_signed_bundle(self.bundle, self.public_path)
        self.assertEqual(manifest["platform"], "test-platform")

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

    def test_rejects_trust_anchor_inside_bundle(self):
        in_bundle = self.bundle / "release-public.pem"
        in_bundle.write_bytes(self.public_path.read_bytes())
        with self.assertRaisesRegex(BootstrapError, "outside the removable bundle"):
            verify_signed_bundle(self.bundle, in_bundle)

    def test_rejects_missing_signature(self):
        (self.bundle / "runtime-manifest.sig").unlink()
        with self.assertRaisesRegex(BootstrapError, "missing"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_rejects_unexpected_file(self):
        (self.bundle / "extra.bin").write_bytes(b"unapproved")
        with self.assertRaisesRegex(BootstrapError, "inventory or file hashes"):
            verify_signed_bundle(self.bundle, self.public_path)

    def test_staging_revalidates_host_controlled_copy(self):
        with stage_verified_bundle(self.bundle, self.public_path, self.root / "host-temp") as temp_dir:
            staged = Path(temp_dir)
            self.assertTrue((staged / "payload.txt").is_file())
            self.assertEqual((staged / "payload.txt").read_text(encoding="utf-8"), "verified payload")
            verify_signed_bundle(staged, self.public_path)

    @unittest.skipIf(os.name == "nt", "shell executable smoke test is POSIX-specific")
    def test_launches_only_signed_entrypoint_from_staging(self):
        result = run_verified_bundle(
            self.bundle,
            self.public_path,
            "bin/yuki",
        )
        self.assertEqual(result, 0)

    def test_rejects_unsigned_or_unlisted_entrypoint(self):
        with self.assertRaisesRegex(BootstrapError, "not part of the signed"):
            run_verified_bundle(
                self.bundle,
                self.public_path,
                "payload.txt",
            )


if __name__ == "__main__":
    unittest.main()
