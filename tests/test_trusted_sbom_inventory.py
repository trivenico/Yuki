import unittest
import json
import os
import tempfile
from pathlib import Path

from core.host_runtime_inventory import RuntimeInventoryError
from core.trusted_sbom_inventory import (
    DigestBoundSBOMVerifier,
    ReadOnlySBOMManifestStore,
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

    def test_preserves_path_qualified_nested_dependency_instances(self):
        unsigned = dict(self.unsigned, installed_dependencies={
            "npm:shared#usr/lib/node_modules/alpha/node_modules/shared": "1.0.0",
            "npm:shared#usr/lib/node_modules/beta/node_modules/shared": "2.0.0",
        })
        result = self.verifier.verify(self.signed(unsigned), expected_image_ref=IMAGE)
        self.assertEqual(
            result["installed_dependencies"],
            {
                "npm:shared#usr/lib/node_modules/alpha/node_modules/shared": "1.0.0",
                "npm:shared#usr/lib/node_modules/beta/node_modules/shared": "2.0.0",
            },
        )

    def test_preserves_case_sensitive_npm_install_locations(self):
        unsigned = dict(self.unsigned, installed_dependencies={
            "npm:shared#app/node_modules/Parent/node_modules/shared": "1.0.0",
            "npm:shared#app/node_modules/parent/node_modules/shared": "2.0.0",
        })
        result = self.verifier.verify(self.signed(unsigned), expected_image_ref=IMAGE)
        self.assertEqual(len(result["installed_dependencies"]), 2)

    def test_preserves_distinct_npm_paths_with_punctuation(self):
        unsigned = dict(self.unsigned, installed_dependencies={
            "npm:foo.bar#app/node_modules/foo.bar": "1.0.0",
            "npm:foo-bar#app/node_modules/foo-bar": "2.0.0",
        })
        result = self.verifier.verify(self.signed(unsigned), expected_image_ref=IMAGE)
        self.assertEqual(len(result["installed_dependencies"]), 2)

    def test_accepts_large_mixed_ecosystem_inventory_without_collapsing_npm_paths(self):
        dependencies = {
            "PyYAML": "6.0.3",
            "deb:libssl3": "3.0.13",
            "npm:shared#app/node_modules/Parent/node_modules/shared": "1.0.0",
            "npm:shared#app/node_modules/parent/node_modules/shared": "2.0.0",
        }
        dependencies.update({f"python-package-{index}": "1.0.0" for index in range(130)})
        unsigned = dict(self.unsigned, installed_dependencies=dependencies)
        result = self.verifier.verify(self.signed(unsigned), expected_image_ref=IMAGE)
        self.assertEqual(len(result["installed_dependencies"]), 134)
        self.assertEqual(result["installed_dependencies"]["deb:libssl3"], "3.0.13")
        self.assertEqual(
            result["installed_dependencies"]["npm:shared#app/node_modules/Parent/node_modules/shared"],
            "1.0.0",
        )

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


class SBOMRuntimeComparisonTests(unittest.TestCase):
    def test_live_runtime_must_match_verified_sbom_exactly(self):
        from core.trusted_sbom_inventory import build_sbom_runtime_inventory_collector

        expected = {
            "runtime_versions": {"python": "3.12.2"},
            "installed_dependencies": {"requests": "2.32.0"},
        }
        collector = build_sbom_runtime_inventory_collector(
            sbom_collector=lambda *_: expected,
            runtime_observer=lambda *_: {
                "runtime_versions": {"Python": "3.12.2"},
                "installed_dependencies": {"Requests": "2.32.0"},
            },
        )
        self.assertEqual(
            collector("task", "worker", "c" * 64, IMAGE, "spec"),
            expected,
        )

    def test_live_runtime_version_or_package_drift_fails_closed(self):
        from core.trusted_sbom_inventory import build_sbom_runtime_inventory_collector

        expected = {
            "runtime_versions": {"python": "3.12.2"},
            "installed_dependencies": {"requests": "2.32.0"},
        }
        for observed in (
            {"runtime_versions": {"python": "3.13.0"}, "installed_dependencies": {"requests": "2.32.0"}},
            {"runtime_versions": {"python": "3.12.2"}, "installed_dependencies": {"requests": "9.9.9"}},
            {"runtime_versions": {"python": "3.12.2"}, "installed_dependencies": {}},
        ):
            collector = build_sbom_runtime_inventory_collector(
                sbom_collector=lambda *_: expected,
                runtime_observer=lambda *_, value=observed: value,
            )
            with self.subTest(observed=observed):
                with self.assertRaises(RuntimeInventoryError):
                    collector("task", "worker", "c" * 64, IMAGE, "spec")

    def test_missing_or_malformed_live_runtime_inventory_fails_closed(self):
        from core.trusted_sbom_inventory import build_sbom_runtime_inventory_collector

        expected = {
            "runtime_versions": {"python": "3.12.2"},
            "installed_dependencies": {"requests": "2.32.0"},
        }
        collector = build_sbom_runtime_inventory_collector(
            sbom_collector=lambda *_: expected,
            runtime_observer=lambda *_: (_ for _ in ()).throw(OSError("probe unavailable")),
        )
        with self.assertRaises(RuntimeInventoryError):
            collector("task", "worker", "c" * 64, IMAGE, "spec")
        malformed = build_sbom_runtime_inventory_collector(
            sbom_collector=lambda *_: expected,
            runtime_observer=lambda *_: {"runtime_versions": {}, "installed_dependencies": {}, "extra": {}},
        )
        with self.assertRaises(RuntimeInventoryError):
            malformed("task", "worker", "c" * 64, IMAGE, "spec")


class ReadOnlySBOMManifestStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        os.chmod(self.directory.name, 0o700)
        self.store_path = Path(self.directory.name)
        self.manifest = {
            "schema_version": 1, "image_ref": IMAGE,
            "generated_at": NOW - 60, "expires_at": NOW + 3600,
            "runtime_versions": {"Python": "3.12.2"},
            "installed_dependencies": {"Requests": "2.32.0"},
        }
        self.manifest["signature"] = sign_sbom_manifest(self.manifest, KEY)
        self.file_path = self.store_path / ReadOnlySBOMManifestStore.filename_for(IMAGE)
        self.file_path.write_text(json.dumps(self.manifest), encoding="ascii")
        os.chmod(self.file_path, 0o400)

    def tearDown(self):
        self.directory.cleanup()

    def test_reads_manifest_from_digest_indexed_private_store(self):
        store = ReadOnlySBOMManifestStore(directory=str(self.store_path), expected_uid=os.getuid())
        self.assertEqual(store(IMAGE), self.manifest)

    def test_rejects_missing_symlink_and_unsafe_manifest_files(self):
        store = ReadOnlySBOMManifestStore(directory=str(self.store_path), expected_uid=os.getuid())
        self.file_path.unlink()
        with self.assertRaises(RuntimeInventoryError):
            store(IMAGE)
        outside = self.store_path / "outside.json"
        outside.write_text(json.dumps(self.manifest), encoding="ascii")
        os.chmod(outside, 0o400)
        self.file_path.symlink_to(outside)
        with self.assertRaises(RuntimeInventoryError):
            store(IMAGE)
        self.file_path.unlink()
        self.file_path.write_text(json.dumps(self.manifest), encoding="ascii")
        os.chmod(self.file_path, 0o440)
        with self.assertRaises(RuntimeInventoryError):
            store(IMAGE)

    def test_rejects_duplicate_json_keys_and_hard_links(self):
        store = ReadOnlySBOMManifestStore(directory=str(self.store_path), expected_uid=os.getuid())
        self.file_path.write_text('{"schema_version":1,"schema_version":1}', encoding="ascii")
        os.chmod(self.file_path, 0o400)
        with self.assertRaises(RuntimeInventoryError):
            store(IMAGE)
        self.file_path.unlink()
        self.file_path.write_text(json.dumps(self.manifest), encoding="ascii")
        os.chmod(self.file_path, 0o400)
        os.link(self.file_path, self.store_path / "alias.json")
        with self.assertRaises(RuntimeInventoryError):
            store(IMAGE)

    def test_rejects_group_or_world_writable_store_directory(self):
        os.chmod(self.store_path, 0o770)
        with self.assertRaises(RuntimeInventoryError):
            ReadOnlySBOMManifestStore(directory=str(self.store_path), expected_uid=os.getuid())


if __name__ == "__main__":
    unittest.main()
