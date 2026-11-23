import json
import unittest

from core.host_runtime_inventory import RuntimeInventoryError
from core.npm_package_lock_inventory import classify_npm_package_lock, parse_npm_package_lock


class NpmPackageLockInventoryTests(unittest.TestCase):
    def test_parses_v2_and_v3_nested_and_scoped_instances(self):
        for version in (2, 3):
            document = {
                "name": "app",
                "lockfileVersion": version,
                "packages": {
                    "": {"name": "app", "version": "1.0.0"},
                    "node_modules/alpha": {"version": "1.0.0"},
                    "node_modules/alpha/node_modules/shared": {"version": "2.0.0"},
                    "node_modules/beta/node_modules/shared": {"version": "3.0.0"},
                    "node_modules/@scope/pkg": {"version": "4.0.0"},
                    "node_modules/alpha/node_modules/@scope/child": {"version": "5.0.0"},
                },
            }
            result = parse_npm_package_lock(json.dumps(document))
            self.assertEqual(len(result), 5)
            self.assertEqual(
                result["npm:shared#app/node_modules/alpha/node_modules/shared"], "2.0.0",
            )
            self.assertEqual(
                result["npm:shared#app/node_modules/beta/node_modules/shared"], "3.0.0",
            )
            self.assertEqual(
                result["npm:@scope/pkg#app/node_modules/@scope/pkg"], "4.0.0",
            )
            self.assertEqual(
                result["npm:@scope/child#app/node_modules/alpha/node_modules/@scope/child"],
                "5.0.0",
            )


    def test_classifies_production_development_optional_and_bundled_entries(self):
        document = {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/prod": {"version": "1.0.0"},
                "node_modules/dev": {"version": "1.0.0", "dev": True},
                "node_modules/optional": {"version": "1.0.0", "optional": True},
                "node_modules/dev-opt": {
                    "version": "1.0.0", "dev": True, "optional": True,
                    "devOptional": True,
                },
                "node_modules/bundled": {"version": "1.0.0", "inBundle": True},
            },
        }
        raw = json.dumps(document)
        classes = classify_npm_package_lock(raw)
        self.assertEqual(classes["npm:prod#app/node_modules/prod"], "production")
        self.assertEqual(classes["npm:dev#app/node_modules/dev"], "development")
        self.assertEqual(classes["npm:optional#app/node_modules/optional"], "optional")
        self.assertEqual(classes["npm:dev-opt#app/node_modules/dev-opt"], "development_optional")
        self.assertEqual(classes["npm:bundled#app/node_modules/bundled"], "production_bundled")
        self.assertEqual(parse_npm_package_lock(raw)["npm:dev#app/node_modules/dev"], "1.0.0")

    def test_rejects_malformed_or_conflicting_dependency_classification_flags(self):
        for metadata in (
            {"version": "1.0.0", "dev": "true"},
            {"version": "1.0.0", "devOptional": True},
            {"version": "1.0.0", "optional": 1},
        ):
            with self.subTest(metadata=metadata), self.assertRaises(RuntimeInventoryError):
                classify_npm_package_lock(json.dumps({
                    "lockfileVersion": 3,
                    "packages": {"node_modules/pkg": metadata},
                }))

    def test_rejects_unsupported_versions_and_missing_packages_map(self):
        for document in (
            {"lockfileVersion": 1, "packages": {}},
            {"lockfileVersion": 4, "packages": {}},
            {"lockfileVersion": 3},
        ):
            with self.subTest(document=document), self.assertRaises(RuntimeInventoryError):
                parse_npm_package_lock(json.dumps(document))

    def test_rejects_duplicate_json_keys(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_npm_package_lock(
                '{"lockfileVersion":3,"lockfileVersion":3,"packages":{}}'
            )

    def test_rejects_traversal_malformed_path_and_name_mismatch(self):
        for path, metadata in (
            ("node_modules/../escape", {"version": "1.0.0"}),
            ("node_modules/alpha/extra", {"version": "1.0.0"}),
            ("node_modules/alpha", {"name": "other", "version": "1.0.0"}),
            ("node_modules/@scope", {"version": "1.0.0"}),
        ):
            document = {"lockfileVersion": 3, "packages": {path: metadata}}
            with self.subTest(path=path), self.assertRaises(RuntimeInventoryError):
                parse_npm_package_lock(json.dumps(document))

    def test_rejects_linked_workspace_and_missing_version_entries(self):
        for metadata in ({"link": True, "version": "1.0.0"}, {}):
            document = {
                "lockfileVersion": 3,
                "packages": {"node_modules/workspace": metadata},
            }
            with self.subTest(metadata=metadata), self.assertRaises(RuntimeInventoryError):
                parse_npm_package_lock(json.dumps(document))

    def test_rejects_oversized_and_invalid_utf8_inputs(self):
        with self.assertRaises(RuntimeInventoryError):
            parse_npm_package_lock(" " * (4 * 1024 * 1024 + 1))
        with self.assertRaises(RuntimeInventoryError):
            parse_npm_package_lock(b"\xff")


if __name__ == "__main__":
    unittest.main()
