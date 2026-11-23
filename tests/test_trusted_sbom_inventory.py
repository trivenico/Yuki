import unittest

from core.host_runtime_inventory import RuntimeInventoryError
from core.trusted_sbom_inventory import (
    DigestBoundSBOMVerifier,
    build_sbom_inventory_collector,
    sign_sbom_manifest,
)

KEY = b"trusted-build-pipeline-sbom-publisher-key-32-bytes"
NOW = 1_800_000_000
IMAGE = "registry.example/yuki/python@sha256:" + "a" * 64


class DigestBoundSBOMVerifierTests(unittest.TestCase):
    def setUp(self):
        self.unsigned = {
            "schema_version": 1,
            "image_ref": IMAGE,
            "generated_at": NOW - 60,
            "expires_at": NOW + 3600,
            "runtime_versions": {"Python": "3.12.2"},
            "installed_dependencies": {"Requests": "2.32.0", "my_package": "1.4.0"},
        }
        self.verifier = DigestBoundSBOMVerifier(
            publisher_key=KEY, clock=lambda: NOW,
        )

    def signed(self, unsigned=None):
        value = dict(unsigned or self.unsigned)
        return {**value, "signature": sign_sbom_manifest(value, KEY)}

    def test_verifies_signed_inventory_and_normalizes_names(self):
        result = self.verifier.verify(self.signed(), expected_image_ref=IMAGE)
        self.assertEqual(result, {
            "runtime_versions": {"python": "3.12.2"},
            "installed_dependencies": {"requests": "2.32.0", "my-package": "1.4.0"},
        })

    def test_rejects_unsigned_or_tampered_manifest(self):
        with self.assertRaises(RuntimeInventoryError):
            self.verifier.verify(self.unsigned, expected_image_ref=IMAGE)
        tampered = self.signed()
        tampered["installed_dependencies"] = {"requests": "9.9.9"}
        with self.assertRaises(RuntimeInventoryError):
            self.verifier.verify(tampered, expected_image_ref=IMAGE)

    def test_rejects_wrong_or_mutable_image_reference(self):
        with self.assertRaises(RuntimeInventoryError):
            self.verifier.verify(self.signed(), expected_image_ref=IMAGE.replace("a" * 64, "b" * 64))
        with self.assertRaises(RuntimeInventoryError):
            self.verifier.verify(self.signed(), expected_image_ref="python:latest")

    def test_rejects_expired_future_and_overlong_validity(self):
        expired = dict(self.unsigned, generated_at=NOW - 5000, expires_at=NOW - 1)
        future = dict(self.unsigned, generated_at=NOW + 400, expires_at=NOW + 500)
        overlong = dict(self.unsigned, generated_at=NOW, expires_at=NOW + 8 * 24 * 3600)
        for manifest in (expired, future, overlong):
            with self.subTest(manifest=manifest):
                with self.assertRaises(RuntimeInventoryError):
                    self.verifier.verify(self.signed(manifest), expected_image_ref=IMAGE)

    def test_rejects_invalid_schema_and_duplicate_normalized_names(self):
        wrong_schema = dict(self.unsigned, schema_version=2)
        duplicate = dict(self.unsigned, installed_dependencies={
            "my-package": "1.0", "my_package": "2.0",
        })
        for manifest in (wrong_schema, duplicate):
            with self.subTest(manifest=manifest):
                with self.assertRaises(RuntimeInventoryError):
                    self.verifier.verify(self.signed(manifest), expected_image_ref=IMAGE)

    def test_digest_indexed_collector_fails_closed_on_missing_or_bad_manifest(self):
        store = {IMAGE: self.signed()}
        collector = build_sbom_inventory_collector(
            manifest_lookup=lambda image: store[image], verifier=self.verifier,
        )
        result = collector("task", "worker", "c" * 64, IMAGE, "spec")
        self.assertEqual(result["runtime_versions"], {"python": "3.12.2"})
        with self.assertRaises(RuntimeInventoryError):
            collector("task", "worker", "c" * 64, IMAGE.replace("a" * 64, "b" * 64), "spec")
        broken = build_sbom_inventory_collector(
            manifest_lookup=lambda _image: (_ for _ in ()).throw(KeyError("missing")),
            verifier=self.verifier,
        )
        with self.assertRaises(RuntimeInventoryError):
            broken("task", "worker", "c" * 64, IMAGE, "spec")


if __name__ == "__main__":
    unittest.main()
