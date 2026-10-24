"""Metadata-only skill discovery with explicit approval before registration.

Unlike legacy code discovery, this module never imports Python modules or
executes candidate code. Reviewed code must be instantiated by a trusted caller
and passed to register_approved_skill separately.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
_ALLOWED_RISKS = {"low", "medium", "high", "critical"}
_ALLOWED_PERMISSIONS = {
    "read_workspace", "write_workspace", "network", "system", "memory_read",
    "memory_write", "external_account",
}
_MAX_MANIFEST_BYTES = 16 * 1024
_MAX_CANDIDATES = 500
_MAX_DESCRIPTION = 1000
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


class SkillDiscoveryError(ValueError):
    """Raised when a manifest or registration request is invalid."""


@dataclass(frozen=True)
class SkillCandidate:
    name: str
    description: str
    risk: str
    permissions: tuple[str, ...]
    manifest_path: str
    entrypoint: str
    content_sha256: str
    status: str = "pending_review"


def _safe_relative_entrypoint(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise SkillDiscoveryError("entrypoint must be a short relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise SkillDiscoveryError("entrypoint must be a relative Python path without traversal")
    return path.as_posix()


def _parse_manifest(path: Path, root: Path) -> SkillCandidate:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SkillDiscoveryError("manifest cannot be read") from exc
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise SkillDiscoveryError("manifest exceeds size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SkillDiscoveryError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise SkillDiscoveryError("manifest must be a JSON object")

    name = data.get("name")
    description = data.get("description")
    risk = data.get("risk", "high")
    permissions = data.get("permissions", [])
    entrypoint = _safe_relative_entrypoint(data.get("entrypoint"))
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise SkillDiscoveryError("invalid skill name")
    if not isinstance(description, str) or not description.strip() or len(description) > _MAX_DESCRIPTION:
        raise SkillDiscoveryError("invalid skill description")
    if not isinstance(risk, str) or risk not in _ALLOWED_RISKS:
        raise SkillDiscoveryError("invalid skill risk")
    if not isinstance(permissions, list) or len(permissions) > len(_ALLOWED_PERMISSIONS):
        raise SkillDiscoveryError("permissions must be a bounded list")
    if any(not isinstance(item, str) or item not in _ALLOWED_PERMISSIONS for item in permissions):
        raise SkillDiscoveryError("unknown permission requested")
    if len(set(permissions)) != len(permissions):
        raise SkillDiscoveryError("duplicate permission")
    root_resolved = root.resolve()
    try:
        relative_manifest = path.resolve().relative_to(root_resolved).as_posix()
    except ValueError as exc:
        raise SkillDiscoveryError("manifest escaped discovery root") from exc
    return SkillCandidate(
        name=name,
        description=description.strip(),
        risk=risk,
        permissions=tuple(sorted(permissions)),
        manifest_path=relative_manifest,
        entrypoint=entrypoint,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def discover_skill_manifests(root: str | Path) -> tuple[SkillCandidate, ...]:
    """Discover bounded JSON manifests without importing or executing Python."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise SkillDiscoveryError("discovery root must be an existing directory")
    candidates = []
    seen = set()
    count = 0
    for path in sorted(root_path.rglob("*.skill.json")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        count += 1
        if count > _MAX_CANDIDATES:
            raise SkillDiscoveryError("too many skill manifests")
        candidate = _parse_manifest(path, root_path)
        if candidate.name in seen:
            raise SkillDiscoveryError(f"duplicate discovered skill name: {candidate.name}")
        seen.add(candidate.name)
        candidates.append(candidate)
    return tuple(candidates)


def register_approved_skill(
    registry: Any,
    candidate: SkillCandidate,
    skill: Any,
    *,
    approved_names: set[str] | frozenset[str],
    verified_sha256: str,
) -> None:
    """Register a separately instantiated skill only after explicit review."""
    if not isinstance(candidate, SkillCandidate):
        raise SkillDiscoveryError("candidate has invalid type")
    if candidate.status != "pending_review":
        raise SkillDiscoveryError("candidate status is invalid")
    if candidate.name not in approved_names:
        raise SkillDiscoveryError("skill has not been explicitly approved")
    if verified_sha256 != candidate.content_sha256:
        raise SkillDiscoveryError("manifest changed since review")
    if getattr(skill, "name", None) != candidate.name:
        raise SkillDiscoveryError("instantiated skill name does not match manifest")
    if not callable(getattr(skill, "execute", None)):
        raise SkillDiscoveryError("skill must implement execute()")
    register = getattr(registry, "register", None)
    if not callable(register):
        raise SkillDiscoveryError("registry does not support registration")
    register(skill)
