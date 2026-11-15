import unittest
from pathlib import Path
from dataclasses import replace

from core.component_catalog import (
    COMPONENT_STATUSES,
    DEFAULT_COMPONENTS,
    ComponentContract,
    component_catalog_as_dicts,
    component_catalog_json,
    validate_component_catalog,
)


class ComponentCatalogTests(unittest.TestCase):
    def test_default_catalog_is_valid_against_repository(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_component_catalog(repository_root=root), [])

    def test_component_ids_are_unique_and_statuses_are_valid(self):
        ids = [item.component_id for item in DEFAULT_COMPONENTS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(item.status in COMPONENT_STATUSES for item in DEFAULT_COMPONENTS))

    def test_dependencies_resolve_and_catalog_has_major_domains(self):
        ids = {item.component_id for item in DEFAULT_COMPONENTS}
        self.assertTrue({
            "identity-access", "provider-runtime", "agent-execution",
            "durable-conversations", "application-composition", "guarded-actions",
            "durable-workflows", "memory", "business-integrations", "conversation-api",
            "mcp-integrations", "voice-runtime", "operator-interface",
        }.issubset(ids))
        for item in DEFAULT_COMPONENTS:
            self.assertTrue(set(item.depends_on).issubset(ids))

    def test_implemented_requires_test_evidence(self):
        contract = ComponentContract(
            component_id="example", name="Example", layer="domain",
            status="implemented", purpose="Example purpose",
            entrypoints=("example.py",), depends_on=(),
            public_contract=("example operation",), test_evidence=(),
        )
        self.assertIn("implemented components require test evidence", contract.validate())

    def test_unknown_dependency_is_rejected(self):
        item = replace(DEFAULT_COMPONENTS[0], depends_on=("missing-component",))
        errors = validate_component_catalog(
            [item], repository_root=Path(__file__).resolve().parents[1]
        )
        self.assertTrue(any("unknown dependency" in error for error in errors))

    def test_dependency_cycle_is_rejected(self):
        first = replace(DEFAULT_COMPONENTS[0], depends_on=("second",))
        second = replace(DEFAULT_COMPONENTS[1], component_id="second",
                         depends_on=("identity-access",))
        errors = validate_component_catalog(
            [first, second], repository_root=Path(__file__).resolve().parents[1]
        )
        self.assertTrue(any("dependency cycle" in error for error in errors))

    def test_unsafe_paths_are_rejected(self):
        item = replace(DEFAULT_COMPONENTS[0], entrypoints=("../outside.py",))
        errors = validate_component_catalog(
            [item], repository_root=Path(__file__).resolve().parents[1]
        )
        self.assertTrue(any("unsafe repository path" in error for error in errors))

    def test_symlinked_entrypoint_cannot_escape_repository_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "repository"
            root.mkdir()
            outside = Path(parent) / "outside.py"
            outside.write_text("# outside repository", encoding="utf-8")
            link = root / "entrypoint.py"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            evidence = root / "test_evidence.py"
            evidence.write_text("# test evidence", encoding="utf-8")
            contract = ComponentContract(
                component_id="symlink-check", name="Symlink check",
                layer="domain", status="experimental", purpose="Path containment",
                entrypoints=("entrypoint.py",), depends_on=(),
                public_contract=("path containment",),
                test_evidence=("test_evidence.py",),
            )
            errors = validate_component_catalog([contract], repository_root=root)
            self.assertTrue(any("escapes root" in error for error in errors))

    def test_catalog_exports_json_safe_data(self):
        import json
        data = json.loads(component_catalog_json())
        self.assertEqual(data[0]["component_id"], DEFAULT_COMPONENTS[0].component_id)
        self.assertEqual(len(data), len(component_catalog_as_dicts()))


if __name__ == "__main__":
    unittest.main()
