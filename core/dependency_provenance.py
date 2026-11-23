"""Canonical, backward-compatible identities for verified installed dependencies.

The authenticated inventory remains a mapping of identity to exact version.
This module adds strict validation and collision-safe composition without
changing the signed SBOM or host-agent wire schema. It does not turn lockfile
declarations or package roles into installation evidence.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from core.host_runtime_inventory import RuntimeInventoryError

_MAX_DEPENDENCIES = 4096
_NAME = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,127}$")
_DEB_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
_NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]{0,63}/)?[a-z0-9][a-z0-9._~-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,63}$")


def parse_dependency_identity(identity: str) -> tuple[str, str, str | None]:
    """Return (ecosystem, normalized name, install location) for legacy keys."""
    if type(identity) is not str or not identity or len(identity) > 256:
        raise RuntimeInventoryError("dependency identity is invalid.")
    if identity.startswith("npm:"):
        package, separator, location = identity[4:].partition("#")
        if not separator or not _NPM_NAME.fullmatch(package) or not location:
            raise RuntimeInventoryError("npm dependency identity is malformed.")
        if (location.startswith("/") or location.endswith("/")
                or "\\" in location
                or any(part in {"", ".", ".."} for part in location.split("/"))
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in location)):
            raise RuntimeInventoryError("npm dependency install location is unsafe.")
        return "npm", package.lower(), location
    if identity.startswith("deb:"):
        name = identity[4:].lower()
        if not _DEB_NAME.fullmatch(name):
            raise RuntimeInventoryError("Debian dependency identity is malformed.")
        return "debian", name, None
    name = re.sub(r"[-_.]+", "-", identity).lower()
    if not _NAME.fullmatch(name):
        raise RuntimeInventoryError("Python dependency identity is malformed.")
    return "python", name, None


def validate_installed_dependency_inventory(
    inventory: Mapping[str, str],
) -> dict[str, str]:
    """Validate identity/version pairs and reject normalized identity aliases."""
    if not isinstance(inventory, Mapping) or len(inventory) > _MAX_DEPENDENCIES:
        raise RuntimeInventoryError("installed dependency inventory exceeds its bounds.")
    normalized: dict[str, str] = {}
    for identity, version in inventory.items():
        ecosystem, name, location = parse_dependency_identity(identity)
        if type(version) is not str or not _VERSION.fullmatch(version):
            raise RuntimeInventoryError("installed dependency version is malformed.")
        key = f"npm:{name}#{location}" if ecosystem == "npm" else (
            f"deb:{name}" if ecosystem == "debian" else name
        )
        if key in normalized:
            raise RuntimeInventoryError("installed dependency inventory contains duplicate canonical identities.")
        normalized[key] = version
    return normalized


def merge_installed_dependency_inventories(
    *inventories: Mapping[str, str],
) -> dict[str, str]:
    """Merge verified installed inventories; conflicting versions fail closed."""
    merged: dict[str, str] = {}
    for inventory in inventories:
        validated = validate_installed_dependency_inventory(inventory)
        for identity, version in validated.items():
            previous = merged.get(identity)
            if previous is not None and previous != version:
                raise RuntimeInventoryError(
                    "independent installed dependency inventories disagree on package version."
                )
            merged[identity] = version
            if len(merged) > _MAX_DEPENDENCIES:
                raise RuntimeInventoryError("merged installed dependency inventory exceeds its item ceiling.")
    return merged
