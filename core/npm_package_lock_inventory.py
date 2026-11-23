"""Bounded parser for npm package-lock v2/v3 dependency identities.

This parser is deliberately independent of filesystem discovery. Callers must
first prove that the lockfile belongs to an immutable image-owned application
root and is not under a task-controlled workspace mount.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

from core.host_runtime_inventory import RuntimeInventoryError

_MAX_BYTES = 4 * 1024 * 1024
_MAX_PACKAGES = 20_000
_MAX_PATH = 512
_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]{0,63}/)?[a-z0-9][a-z0-9._~-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _name_from_install_path(value: str) -> str:
    if (type(value) is not str or not value or len(value) > _MAX_PATH
            or value.startswith("/") or "\\" in value or "\x00" in value):
        raise RuntimeInventoryError("npm lockfile contains an invalid package install path.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeInventoryError("npm lockfile package path is not canonical.")
    index = 0
    package_parts: list[str] = []
    while index < len(parts):
        if parts[index] != "node_modules" or index + 1 >= len(parts):
            raise RuntimeInventoryError("npm lockfile package path has an invalid node_modules layout.")
        index += 1
        segment = parts[index]
        if segment.startswith("@"):
            if (not re.fullmatch(r"@[a-z0-9][a-z0-9._~-]{0,63}", segment)
                    or index + 1 >= len(parts)):
                raise RuntimeInventoryError("npm lockfile scoped package path is invalid.")
            package_parts.extend((segment, parts[index + 1]))
            index += 2
        else:
            if not re.fullmatch(r"[a-z0-9][a-z0-9._~-]{0,127}", segment):
                raise RuntimeInventoryError("npm lockfile package path is invalid.")
            package_parts.append(segment)
            index += 1
        if index < len(parts) and parts[index] != "node_modules":
            raise RuntimeInventoryError("npm lockfile package path contains an unexpected segment.")
    name = "/".join(package_parts[-2:]) if len(package_parts) >= 2 and package_parts[-2].startswith("@") else package_parts[-1]
    if not _NAME.fullmatch(name):
        raise RuntimeInventoryError("npm lockfile package name is invalid.")
    return name


def _parse_npm_package_lock_entries(raw: str | bytes) -> dict[str, tuple[str, str]]:
    """Return path-qualified dependency IDs from package-lock v2 or v3.

    The root project entry is not a dependency. Link/workspace entries are
    rejected because their target is not proven to be image-owned here.
    """
    if type(raw) is bytes:
        if len(raw) > _MAX_BYTES:
            raise RuntimeInventoryError("npm package-lock exceeds its byte ceiling.")
        try:
            raw = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RuntimeInventoryError("npm package-lock is not valid UTF-8.") from exc
    elif type(raw) is not str or len(raw.encode("utf-8")) > _MAX_BYTES:
        raise RuntimeInventoryError("npm package-lock input is invalid or exceeds its byte ceiling.")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise RuntimeInventoryError("npm package-lock is malformed or ambiguous JSON.") from exc
    if not isinstance(document, Mapping):
        raise RuntimeInventoryError("npm package-lock root must be an object.")
    lock_version = document.get("lockfileVersion")
    if type(lock_version) is not int or lock_version not in {2, 3}:
        raise RuntimeInventoryError("only npm package-lock versions 2 and 3 are supported.")
    entries = document.get("packages")
    if not isinstance(entries, Mapping) or len(entries) > _MAX_PACKAGES + 1:
        raise RuntimeInventoryError("npm package-lock packages map is missing or exceeds its entry ceiling.")
    if "" in entries and not isinstance(entries[""], Mapping):
        raise RuntimeInventoryError("npm package-lock root package entry is malformed.")
    inventory: dict[str, tuple[str, str]] = {}
    for install_path, metadata in entries.items():
        if install_path == "":
            continue
        name = _name_from_install_path(install_path)
        if not isinstance(metadata, Mapping):
            raise RuntimeInventoryError("npm package-lock dependency entry must be an object.")
        if metadata.get("link") is True:
            raise RuntimeInventoryError("npm package-lock link/workspace dependencies lack image-root provenance.")
        declared_name = metadata.get("name")
        if declared_name is not None and (
            type(declared_name) is not str or declared_name.lower() != name.lower()
        ):
            raise RuntimeInventoryError("npm package-lock path disagrees with its declared package name.")
        version = metadata.get("version")
        if type(version) is not str or not _VERSION.fullmatch(version):
            raise RuntimeInventoryError("npm package-lock dependency has an invalid or missing version.")
        flags = {}
        for flag in ("dev", "optional", "devOptional", "inBundle"):
            value = metadata.get(flag, False)
            if type(value) is not bool:
                raise RuntimeInventoryError("npm package-lock dependency classification flag is malformed.")
            flags[flag] = value
        if flags["devOptional"] and not (flags["dev"] and flags["optional"]):
            raise RuntimeInventoryError("npm package-lock devOptional flag conflicts with dev/optional metadata.")
        if flags["dev"] and flags["optional"]:
            classification = "development_optional"
        elif flags["dev"]:
            classification = "development"
        elif flags["optional"]:
            classification = "optional"
        else:
            classification = "production"
        if flags["inBundle"]:
            classification += "_bundled"
        identity = "npm:" + name.lower() + "#app/" + install_path
        if len(identity) > 128:
            raise RuntimeInventoryError("npm package-lock dependency identity exceeds its path ceiling.")
        if identity in inventory:
            raise RuntimeInventoryError("npm package-lock contains duplicate dependency identities.")
        inventory[identity] = (version, classification)
    return inventory


def parse_npm_package_lock(raw: str | bytes) -> dict[str, str]:
    """Return the identity-to-version view used by existing SBOM parity checks."""
    return {
        identity: version
        for identity, (version, _classification) in _parse_npm_package_lock_entries(raw).items()
    }


def classify_npm_package_lock(raw: str | bytes) -> dict[str, str]:
    """Return production/development/optional classes keyed by package identity.

    Classification is derived from npm lockfile flags. It does not change the
    version-only runtime inventory or imply that a dependency is installed.
    Callers must reconcile identities against the live tree first.
    """
    return {
        identity: classification
        for identity, (_version, classification) in _parse_npm_package_lock_entries(raw).items()
    }
