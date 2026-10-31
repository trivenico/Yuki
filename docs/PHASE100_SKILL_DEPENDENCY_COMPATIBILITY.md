# Phase 100 — read-only skill dependency and compatibility planning

## Goal

Provide validated skill and Python package dependency metadata and an auditable compatibility plan without changing the user's environment.

## Changes

- Add optional `skill_version`, `dependencies.skills`, `dependencies.python`, and `python_compatibility` fields to declarative skill manifests.
- Preserve compatibility with existing manifests by defaulting the skill version to `1.0.0` and dependencies to empty lists.
- Normalize Python package names using PEP 503 separators.
- Validate bounded identifiers, dependency counts, numeric version clauses, and allowed comparison operators.
- Resolve transitive dependencies against already-installed skill manifests in dependency-first order.
- Detect missing and incompatible skill dependencies, missing and incompatible Python distributions, and skill dependency cycles.
- Expose `plan_installed_skill_dependencies()` over the existing extension store and include version/dependency counts in the extension inventory.
- Generate a structured plan with explicit empty side effects and `package_installation_performed: false`.
- Bound the manifest inventory to 500 entries and validate externally supplied package/runtime inventory values.
- Register the capability as experimental with regression-test evidence.

## Manifest example

    {
      "skill_version": "1.2.0",
      "python_compatibility": ">=3.10,<3.14",
      "dependencies": {
        "skills": [{"id": "writing_helper", "version": ">=1.0,<2.0"}],
        "python": [{"name": "PyYAML", "version": ">=6.0,<7.0"}]
      }
    }

## Safety and scope

- Planning is read-only: no pip invocation, package download, subprocess, shell, environment mutation, setup hook, or code execution.
- Skill dependency resolution checks installed declarative skill manifests only.
- Python dependency inspection checks the current interpreter's installed distributions unless a test inventory is explicitly supplied.
- No automatic package resolver or package-index access is included.
- No isolated environment creation or installation workflow is included yet.
- Version constraints intentionally support a limited numeric syntax and are not a complete PEP 440 implementation.
- No CLI or desktop command is wired in this phase.
- Imported skill instructions remain untrusted and their risk/approval policy is unchanged.

## Verification

    python -m unittest tests.test_skill_dependency_manager -v
    python -m unittest tests.test_extension_store tests.test_capability_manifest -v
    python -m unittest discover -s tests -v

Stacked on Phase 99. Keep experimental until exact-head Linux and Windows CI checks pass and parent phases are merged.