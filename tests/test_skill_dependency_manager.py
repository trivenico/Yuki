import unittest

from core.skill_dependency_manager import (
    DependencyDeclarationError, plan_skill_dependencies, validate_skill_dependencies,
    validate_version_specifier, version_satisfies,
)
from core.extension_store import validate_skill_manifest


class SkillDependencyManagerTests(unittest.TestCase):
    def manifest(self, identifier, **overrides):
        value = {
            "type": "yuki_skill", "id": identifier, "version": "1",
            "name": identifier, "description": "A test skill.",
            "instructions": "Use only as task guidance.",
            "skill_version": "1.2.0",
        }
        value.update(overrides)
        return value

    def test_numeric_version_constraints(self):
        self.assertTrue(version_satisfies("1.5.0", ">=1.2,<2.0"))
        self.assertFalse(version_satisfies("2.0.0", ">=1.2,<2.0"))
        self.assertTrue(version_satisfies("1.2", "==1.2.0"))

    def test_rejects_unsupported_version_syntax(self):
        for value in ("*", "~=1.2", ">=1.0; python_version < '3.12'", "abc", ""):
            with self.subTest(value=value), self.assertRaises(DependencyDeclarationError):
                validate_version_specifier(value)

    def test_dependency_declarations_are_bounded_and_normalized(self):
        checked = validate_skill_dependencies({
            "skills": [{"id": "design_tools", "version": ">=1.0,<2.0"}],
            "python": [{"name": "Py_YAML", "version": ">=6.0"}],
        })
        self.assertEqual(checked["python"][0]["name"], "py-yaml")
        with self.assertRaises(DependencyDeclarationError):
            validate_skill_dependencies({"python": [{"name": "yaml", "version": "*"}]})
        with self.assertRaises(DependencyDeclarationError):
            validate_skill_dependencies({"python": [{"name": "yaml", "version": ">=1"}] * 33})

    def test_manifest_preserves_validated_optional_dependency_metadata(self):
        manifest = validate_skill_manifest(self.manifest(
            "release_notes",
            dependencies={"skills": [{"id": "writing_helper", "version": ">=1.0"}],
                          "python": [{"name": "PyYAML", "version": ">=6.0,<7.0"}]},
            python_compatibility=">=3.10,<3.14",
        ))
        self.assertEqual(manifest["dependencies"]["python"][0]["name"], "pyyaml")
        self.assertEqual(manifest["python_compatibility"], ">=3.10,<3.14")

    def test_manifest_rejects_invalid_dependency_metadata(self):
        with self.assertRaisesRegex(ValueError, "dependency metadata"):
            validate_skill_manifest(self.manifest("release_notes", dependencies={"python": ["requests"]}))

    def test_plans_transitive_skill_dependencies_in_dependency_order(self):
        manifests = [
            self.manifest("root", dependencies={"skills": [{"id": "writer", "version": ">=1.0"}]}),
            self.manifest("writer", dependencies={"skills": [{"id": "format", "version": ">=1.0"}]}),
            self.manifest("format"),
        ]
        plan = plan_skill_dependencies(manifests, "root", python_packages={})
        self.assertTrue(plan.ready)
        self.assertEqual(plan.installation_order, ("format", "writer", "root"))
        self.assertEqual(plan.as_dict()["side_effects"], [])
        self.assertFalse(plan.as_dict()["package_installation_performed"])

    def test_reports_missing_and_incompatible_dependencies(self):
        manifests = [self.manifest(
            "root",
            dependencies={
                "skills": [{"id": "missing_skill", "version": ">=1.0"}],
                "python": [{"name": "PyYAML", "version": ">=7.0"},
                           {"name": "requests", "version": ">=2.0"}],
            },
            python_compatibility=">=3.12",
        )]
        plan = plan_skill_dependencies(manifests, "root", python_packages={"pyyaml": "6.0.2", "requests": "2.31.0"}, python_version="3.11.9")
        self.assertFalse(plan.ready)
        self.assertEqual(plan.missing_skills, ("missing_skill",))
        self.assertTrue(any("pyyaml" in item for item in plan.incompatible_python))
        self.assertTrue(any("Python" in item for item in plan.incompatible_python))

    def test_detects_cycles_without_recursing_forever(self):
        manifests = [
            self.manifest("alpha", dependencies={"skills": [{"id": "beta", "version": ">=1.0"}]}),
            self.manifest("beta", dependencies={"skills": [{"id": "alpha", "version": ">=1.0"}]}),
        ]
        plan = plan_skill_dependencies(manifests, "alpha", python_packages={})
        self.assertFalse(plan.ready)
        self.assertTrue(plan.cycles)

    def test_invalid_package_inventory_and_runtime_version_fail_closed(self):
        manifests = [self.manifest(
            "root", dependencies={"skills": [], "python": [{"name": "requests", "version": ">=2.0"}]}
        )]
        plan = plan_skill_dependencies(
            manifests, "root", python_packages={"requests": "latest"}, python_version=3.12
        )
        self.assertFalse(plan.ready)
        self.assertTrue(any("invalid version" in error for error in plan.errors))
        self.assertTrue(any("Current Python version" in error for error in plan.errors))

    def test_bounds_manifest_inventory(self):
        manifests = (self.manifest(f"skill_{index}") for index in range(501))
        plan = plan_skill_dependencies(manifests, "skill_0", python_packages={})
        self.assertFalse(plan.ready)
        self.assertTrue(any("maximum 500" in error for error in plan.errors))

    def test_rejects_duplicate_manifest_ids_and_invalid_root(self):
        plan = plan_skill_dependencies([self.manifest("alpha"), self.manifest("alpha")], "alpha", python_packages={})
        self.assertFalse(plan.ready)
        self.assertTrue(any("Duplicate installed skill id" in error for error in plan.errors))


if __name__ == "__main__":
    unittest.main()