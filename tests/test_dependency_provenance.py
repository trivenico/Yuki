import unittest

from core.host_runtime_inventory import RuntimeInventoryError
from core.dependency_provenance import (
    merge_installed_dependency_inventories,
    parse_dependency_identity,
    validate_installed_dependency_inventory,
)


class DependencyProvenanceTests(unittest.TestCase):
    def test_parses_python_debian_and_path_qualified_npm_identities(self):
        self.assertEqual(parse_dependency_identity("Requests_pkg"), ("python", "requests-pkg", None))
        self.assertEqual(parse_dependency_identity("deb:libssl3"), ("debian", "libssl3", None))
        self.assertEqual(
            parse_dependency_identity("npm:@scope/pkg#app/node_modules/@scope/pkg"),
            ("npm", "@scope/pkg", "app/node_modules/@scope/pkg"),
        )

    def test_preserves_npm_locations_and_canonicalizes_python_names(self):
        result = validate_installed_dependency_inventory({
            "Requests_pkg": "2.32.0",
            "npm:shared#app/node_modules/shared": "1.0.0",
            "npm:shared#app/node_modules/parent/node_modules/shared": "2.0.0",
            "deb:libssl3": "3.0.2-1",
        })
        self.assertEqual(result["requests-pkg"], "2.32.0")
        self.assertEqual(len([key for key in result if key.startswith("npm:")]), 2)
        self.assertEqual(result["deb:libssl3"], "3.0.2-1")

    def test_rejects_normalized_python_aliases_and_unsafe_npm_paths(self):
        with self.assertRaises(RuntimeInventoryError):
            validate_installed_dependency_inventory({"my_pkg": "1.0", "my.pkg": "1.0"})
        for identity in (
            "npm:demo#../outside",
            "npm:demo#/absolute/path",
            "npm:demo#app/node_modules/demo\\package",
            "npm:demo#app//node_modules/demo",
        ):
            with self.subTest(identity=identity):
                with self.assertRaises(RuntimeInventoryError):
                    validate_installed_dependency_inventory({identity: "1.0.0"})

    def test_merge_deduplicates_identical_evidence_and_rejects_version_conflicts(self):
        self.assertEqual(
            merge_installed_dependency_inventories(
                {"Requests_pkg": "2.32.0"},
                {"requests-pkg": "2.32.0", "deb:libc6": "2.36"},
            ),
            {"requests-pkg": "2.32.0", "deb:libc6": "2.36"},
        )
        with self.assertRaises(RuntimeInventoryError):
            merge_installed_dependency_inventories(
                {"requests": "2.32.0"}, {"Requests": "9.9.9"},
            )

    def test_rejects_invalid_versions_and_unbounded_inventories(self):
        for value in ("", "not a version", "x" * 65):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeInventoryError):
                    validate_installed_dependency_inventory({"demo": value})


if __name__ == "__main__":
    unittest.main()
