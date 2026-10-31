"""Plan isolated, hash-locked skill environments without executing commands.

All APIs in this module are pure planning/validation helpers. Provisioning must be
performed by a separately authorized executor with an OS sandbox and audit trail.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.skill_dependency_manager import DependencyDeclarationError, _version_tuple, normalize_package_name

_SKILL_ID = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_LOCK_PACKAGES = 128


class SkillEnvironmentError(ValueError):
    """Raised when an environment or lockfile request is unsafe."""


@dataclass(frozen=True)
class SkillEnvironmentPlan:
    skill_id: str
    skill_version: str
    environment_path: str
    lockfile_path: str
    lockfile_content: str
    lockfile_sha256: str
    create_venv_argv: tuple[str, ...]
    install_argv: tuple[str, ...]
    package_count: int
    side_effects: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "environment_path": self.environment_path,
            "lockfile_path": self.lockfile_path,
            "lockfile_content": self.lockfile_content,
            "lockfile_sha256": self.lockfile_sha256,
            "create_venv_argv": list(self.create_venv_argv),
            "install_argv": list(self.install_argv),
            "package_count": self.package_count,
            "side_effects": list(self.side_effects),
            "commands_executed": False,
            "network_access": False,
        }


def validate_hashed_lockfile(entries: Any) -> tuple[tuple[str, str, str], ...]:
    """Validate a complete pinned package list with one SHA-256 artifact hash each."""
    if not isinstance(entries, list) or len(entries) > _MAX_LOCK_PACKAGES:
        raise SkillEnvironmentError("Lockfile must be a list of at most 128 package entries.")
    normalized: dict[str, tuple[str, str, str]] = {}
    for item in entries:
        if not isinstance(item, Mapping) or set(item) != {"name", "version", "sha256"}:
            raise SkillEnvironmentError("Each lock entry must contain only name, version, and sha256.")
        name, version, digest = item["name"], item["version"], item["sha256"]
        if not isinstance(name, str) or not _PACKAGE.fullmatch(name):
            raise SkillEnvironmentError("Lockfile contains an invalid package name.")
        try:
            _version_tuple(version)
        except DependencyDeclarationError as exc:
            raise SkillEnvironmentError("Lockfile versions must be numeric and pinned.") from exc
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise SkillEnvironmentError("Lockfile requires a lowercase SHA-256 hash for every package.")
        key = normalize_package_name(name)
        if key in normalized:
            raise SkillEnvironmentError(f"Duplicate lockfile package: {key}.")
        normalized[key] = (key, version, digest)
    return tuple(normalized[key] for key in sorted(normalized))


def render_hashed_requirements(entries: Any) -> str:
    """Render deterministic pip --require-hashes requirements text."""
    packages = validate_hashed_lockfile(entries)
    return "".join(f"{name}=={version} --hash=sha256:{digest}\n" for name, version, digest in packages)


def plan_skill_environment(
    *, skill_id: str, skill_version: str, environment_root: str | os.PathLike[str],
    python_executable: str | os.PathLike[str], wheelhouse: str | os.PathLike[str],
    lock_entries: Any,
) -> SkillEnvironmentPlan:
    """Return exact offline provisioning argv; never create files or launch a process."""
    if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id):
        raise SkillEnvironmentError("Invalid skill identifier.")
    try:
        _version_tuple(skill_version)
    except DependencyDeclarationError as exc:
        raise SkillEnvironmentError("Skill version must be numeric.") from exc

    root = Path(environment_root).expanduser()
    python = Path(python_executable).expanduser()
    wheels = Path(wheelhouse).expanduser()
    for label, path in (("environment root", root), ("Python executable", python), ("wheelhouse", wheels)):
        if not path.is_absolute():
            raise SkillEnvironmentError(f"{label} must be an absolute path.")
    if root.is_symlink() or not root.exists() or not root.is_dir():
        raise SkillEnvironmentError("Environment root must be an existing, non-symlink directory.")
    if python.is_symlink() or not python.exists() or not python.is_file():
        raise SkillEnvironmentError("Python executable must be an existing, non-symlink file.")
    if wheels.is_symlink() or not wheels.exists() or not wheels.is_dir():
        raise SkillEnvironmentError("Wheelhouse must be an existing, non-symlink directory.")
    root = root.resolve(strict=True)
    python = python.resolve(strict=True)
    wheels = wheels.resolve(strict=True)
    skill_root = root / skill_id
    if skill_root.is_symlink() or (skill_root.exists() and not skill_root.is_dir()):
        raise SkillEnvironmentError("Per-skill environment directory must be a non-symlink directory.")
    environment = skill_root / skill_version
    if environment.exists() or environment.is_symlink():
        raise SkillEnvironmentError("Target environment already exists; refusing to overwrite it.")

    lock_content = render_hashed_requirements(lock_entries)
    if not lock_content:
        raise SkillEnvironmentError("An empty lockfile is not a valid environment installation plan.")
    lock_path = root / f".{skill_id}-{skill_version}.requirements.lock"
    if lock_path.exists() or lock_path.is_symlink():
        raise SkillEnvironmentError("Target lockfile already exists; refusing to overwrite it.")

    create_venv = (str(python), "-I", "-m", "venv", str(environment))
    install = (
        str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
        "-I", "-m", "pip", "install", "--isolated", "--disable-pip-version-check",
        "--no-index", "--only-binary=:all:", "--require-hashes",
        "--find-links", str(wheels), "-r", str(lock_path),
    )
    return SkillEnvironmentPlan(
        skill_id, skill_version, str(environment), str(lock_path), lock_content,
        hashlib.sha256(lock_content.encode("utf-8")).hexdigest(), create_venv, install,
        len(validate_hashed_lockfile(lock_entries)),
    )
