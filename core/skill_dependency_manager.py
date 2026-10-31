"""Read-only dependency and compatibility planning for declarative Yuki skills.

This module never installs packages, executes setup hooks, or mutates an environment.
It validates bounded declarations and produces an auditable dependency plan.
"""
from __future__ import annotations

import importlib.metadata
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from itertools import islice
from typing import Any, Iterable, Mapping

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
_SPEC_PART_RE = re.compile(r"^(===|==|!=|>=|<=|>|<)\s*([0-9]+(?:\.[0-9]+){0,3})$")
_NAME_NORMALIZER = re.compile(r"[-_.]+")


class DependencyDeclarationError(ValueError):
    """Raised when a skill dependency declaration is malformed."""


def normalize_package_name(name: str) -> str:
    return _NAME_NORMALIZER.sub("-", name).lower()


def _version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise DependencyDeclarationError("Versions must contain one to four numeric components.")
    return tuple(int(part) for part in value.split("."))


def validate_version_specifier(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 100:
        raise DependencyDeclarationError("Version constraints must be non-empty and at most 100 characters.")
    parts = value.split(",")
    if len(parts) > 8:
        raise DependencyDeclarationError("A version constraint may contain at most eight clauses.")
    for part in parts:
        match = _SPEC_PART_RE.fullmatch(part.strip())
        if not match:
            raise DependencyDeclarationError("Use numeric version clauses such as >=1.2,<2.0.")
        _version_tuple(match.group(2))
    return ",".join(part.strip() for part in parts)


def version_satisfies(version: str, specifier: str) -> bool:
    current = _version_tuple(version)
    for clause in validate_version_specifier(specifier).split(","):
        match = _SPEC_PART_RE.fullmatch(clause)
        assert match is not None
        operator, raw_expected = match.groups()
        expected = _version_tuple(raw_expected)
        width = max(len(current), len(expected))
        left = current + (0,) * (width - len(current))
        right = expected + (0,) * (width - len(expected))
        if operator in {"==", "==="} and left != right:
            return False
        if operator == "!=" and left == right:
            return False
        if operator == ">=" and left < right:
            return False
        if operator == "<=" and left > right:
            return False
        if operator == ">" and left <= right:
            return False
        if operator == "<" and left >= right:
            return False
    return True


def validate_skill_dependencies(value: Any) -> dict[str, list[dict[str, str]]]:
    if value is None:
        return {"skills": [], "python": []}
    if not isinstance(value, dict) or set(value) - {"skills", "python"}:
        raise DependencyDeclarationError("dependencies must contain only skills and python lists.")
    result: dict[str, list[dict[str, str]]] = {"skills": [], "python": []}
    for kind in ("skills", "python"):
        entries = value.get(kind, [])
        if not isinstance(entries, list) or len(entries) > 32:
            raise DependencyDeclarationError(f"dependencies.{kind} must be a list of at most 32 entries.")
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != ({"id", "version"} if kind == "skills" else {"name", "version"}):
                raise DependencyDeclarationError(f"Each dependencies.{kind} entry must contain its identifier and version.")
            identifier = entry["id"] if kind == "skills" else entry["name"]
            if not isinstance(identifier, str) or not (_ID_RE.fullmatch(identifier) if kind == "skills" else _PACKAGE_RE.fullmatch(identifier)):
                raise DependencyDeclarationError(f"Invalid {kind} dependency identifier.")
            normalized = identifier if kind == "skills" else normalize_package_name(identifier)
            if normalized in seen:
                raise DependencyDeclarationError(f"Duplicate {kind} dependency: {normalized}.")
            seen.add(normalized)
            result[kind].append({("id" if kind == "skills" else "name"): normalized, "version": validate_version_specifier(entry["version"])})
    return result


def installed_python_distributions() -> dict[str, str]:
    found: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if name and isinstance(version, str) and _VERSION_RE.fullmatch(version):
            found[normalize_package_name(name)] = version
    return found


@dataclass(frozen=True)
class DependencyPlan:
    skill_id: str
    ready: bool
    installation_order: tuple[str, ...]
    missing_skills: tuple[str, ...]
    incompatible_skills: tuple[str, ...]
    missing_python: tuple[str, ...]
    incompatible_python: tuple[str, ...]
    cycles: tuple[tuple[str, ...], ...]
    errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id, "ready": self.ready,
            "installation_order": list(self.installation_order),
            "missing_skills": list(self.missing_skills),
            "incompatible_skills": list(self.incompatible_skills),
            "missing_python": list(self.missing_python),
            "incompatible_python": list(self.incompatible_python),
            "cycles": [list(cycle) for cycle in self.cycles],
            "errors": list(self.errors),
            "side_effects": [], "package_installation_performed": False,
        }


def plan_skill_dependencies(
    manifests: Iterable[Mapping[str, Any]], skill_id: str, *,
    python_packages: Mapping[str, str] | None = None,
    python_version: str | None = None,
) -> DependencyPlan:
    """Plan a dependency closure against installed skills/packages; never install anything."""
    errors: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    manifest_items = list(islice(manifests, 501))
    if len(manifest_items) > 500:
        return DependencyPlan(skill_id, False, (), (), (), (), (), (), ("Too many installed skill manifests (maximum 500).",))
    for raw in manifest_items:
        if not isinstance(raw, Mapping):
            errors.append("A skill manifest is not an object.")
            continue
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            errors.append("A skill manifest has an invalid id.")
            continue
        if identifier in by_id:
            errors.append(f"Duplicate installed skill id: {identifier}.")
            continue
        try:
            deps = validate_skill_dependencies(raw.get("dependencies"))
            skill_version = raw.get("skill_version", "0.0.0")
            _version_tuple(skill_version)
            python_spec = raw.get("python_compatibility")
            if python_spec is not None:
                python_spec = validate_version_specifier(python_spec)
        except (DependencyDeclarationError, TypeError) as exc:
            errors.append(f"{identifier}: {exc}")
            continue
        by_id[identifier] = {**dict(raw), "dependencies": deps, "skill_version": skill_version}
    if skill_id not in by_id:
        errors.append(f"Requested skill is not installed: {skill_id}.")
        return DependencyPlan(skill_id, False, (), (), (), (), (), (), tuple(errors))

    missing_skills: set[str] = set()
    incompatible_skills: set[str] = set()
    missing_python: set[str] = set()
    incompatible_python: set[str] = set()
    cycles: set[tuple[str, ...]] = set()
    order: list[str] = []
    visiting: list[str] = []
    visited: set[str] = set()
    package_requirements: dict[str, list[tuple[str, str]]] = defaultdict(list)
    python_compatibility: list[tuple[str, str]] = []

    def visit(identifier: str) -> None:
        if identifier in visiting:
            cycle = visiting[visiting.index(identifier):] + [identifier]
            cycles.add(tuple(cycle))
            return
        if identifier in visited or identifier not in by_id:
            return
        visiting.append(identifier)
        manifest = by_id[identifier]
        for dependency in manifest["dependencies"]["skills"]:
            dep_id = dependency["id"]
            target = by_id.get(dep_id)
            if target is None:
                missing_skills.add(dep_id)
                continue
            if not version_satisfies(target["skill_version"], dependency["version"]):
                incompatible_skills.add(f"{dep_id} {dependency[\"version\"]} (installed {target[\"skill_version\"]})")
                continue
            visit(dep_id)
        for dependency in manifest["dependencies"]["python"]:
            package_requirements[dependency["name"]].append((identifier, dependency["version"]))
        if manifest.get("python_compatibility"):
            python_compatibility.append((identifier, manifest["python_compatibility"]))
        visiting.pop()
        visited.add(identifier)
        order.append(identifier)

    visit(skill_id)
    available: dict[str, str] = {}
    raw_inventory = python_packages if python_packages is not None else installed_python_distributions()
    if not isinstance(raw_inventory, Mapping):
        errors.append("Python package inventory must be a mapping of package names to versions.")
        raw_inventory = {}
    for package_name, package_version in raw_inventory.items():
        if not isinstance(package_name, str) or not _PACKAGE_RE.fullmatch(package_name):
            errors.append("Python package inventory contains an invalid package name.")
            continue
        try:
            _version_tuple(package_version)
        except DependencyDeclarationError:
            errors.append(f"Python package inventory contains an invalid version for {package_name}.")
            continue
        available[normalize_package_name(package_name)] = package_version
    for package, requirements in sorted(package_requirements.items()):
        installed = available.get(package)
        if installed is None:
            missing_python.add(package)
            continue
        for owner, constraint in requirements:
            if not version_satisfies(installed, constraint):
                incompatible_python.add(f"{package} {constraint} required by {owner} (installed {installed})")
    current_python = python_version or ".".join(map(str, sys.version_info[:3]))
    if not isinstance(current_python, str) or not _VERSION_RE.fullmatch(current_python):
        errors.append("Current Python version is not a supported numeric version.")
    else:
        for owner, constraint in python_compatibility:
            if not version_satisfies(current_python, constraint):
                incompatible_python.add(f"Python {constraint} required by {owner} (current {current_python})")
    ready = not (errors or missing_skills or incompatible_skills or missing_python or incompatible_python or cycles)
    return DependencyPlan(
        skill_id, ready, tuple(order), tuple(sorted(missing_skills)),
        tuple(sorted(incompatible_skills)), tuple(sorted(missing_python)),
        tuple(sorted(incompatible_python)), tuple(sorted(cycles)), tuple(errors),
    )
