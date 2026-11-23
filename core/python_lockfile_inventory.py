"""Bounded parser for Poetry and uv Python dependency lockfiles.

Lockfiles are metadata, not evidence of installed packages. Callers must
reconcile these identities and versions against independently observed
installed distribution metadata inside a verified image-owned application
root before including them in runtime evidence.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from core.host_runtime_inventory import RuntimeInventoryError

_MAX_BYTES = 4 * 1024 * 1024
_MAX_PACKAGES = 20_000
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,63}$")


def _toml_module():
    try:
        import tomllib
        return tomllib
    except ModuleNotFoundError:
        try:
            import tomli
            return tomli
        except ModuleNotFoundError as exc:
            raise RuntimeInventoryError(
                "Python lockfile parsing requires tomllib (Python 3.11+) or the trusted tomli parser."
            ) from exc


def _decode_document(raw: str | bytes) -> Mapping:
    if type(raw) is bytes:
        if len(raw) > _MAX_BYTES:
            raise RuntimeInventoryError("Python dependency lockfile exceeds its byte ceiling.")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RuntimeInventoryError("Python dependency lockfile is not valid UTF-8.") from exc
    elif type(raw) is str and len(raw.encode("utf-8")) <= _MAX_BYTES:
        text = raw
    else:
        raise RuntimeInventoryError("Python dependency lockfile input is invalid or exceeds its byte ceiling.")
    try:
        document = _toml_module().loads(text)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeInventoryError("Python dependency lockfile is malformed or ambiguous TOML.") from exc
    if not isinstance(document, Mapping):
        raise RuntimeInventoryError("Python dependency lockfile root must be a table.")
    return document


def _normalize_name(value: object) -> str:
    if type(value) is not str or not _NAME.fullmatch(value):
        raise RuntimeInventoryError("Python dependency lockfile contains an invalid package name.")
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_version(value: object) -> str:
    if type(value) is not str or not _VERSION.fullmatch(value):
        raise RuntimeInventoryError("Python dependency lockfile contains an invalid exact package version.")
    return value


def _reject_untrusted_source(package: Mapping) -> None:
    source = package.get("source")
    if source is None:
        return
    if not isinstance(source, Mapping):
        raise RuntimeInventoryError("Python dependency lockfile source metadata is malformed.")
    source_type = source.get("type")
    # Only registry artifacts have a stable registry identity in this
    # inventory. Git, directory, file, editable and workspace references are
    # deliberately excluded until their content provenance is independently
    # established by the host observer.
    if source_type not in {None, "legacy", " PyPI", "pypi"}:
        raise RuntimeInventoryError("VCS, path, file and workspace Python dependencies are unsupported.")
    if any(key in source for key in ("git", "directory", "file", "url", "reference", "resolved_reference")):
        raise RuntimeInventoryError("non-registry Python dependency source is unsupported.")


def _parse_packages(entries: object) -> dict[str, str]:
    if not isinstance(entries, list) or len(entries) > _MAX_PACKAGES:
        raise RuntimeInventoryError("Python dependency lockfile package list is missing or exceeds its entry ceiling.")
    packages: dict[str, str] = {}
    for package in entries:
        if not isinstance(package, Mapping):
            raise RuntimeInventoryError("Python dependency lockfile package entry must be a table.")
        name = _normalize_name(package.get("name"))
        version = _validate_version(package.get("version"))
        _reject_untrusted_source(package)
        if package.get("develop") is True or package.get("editable") is True:
            raise RuntimeInventoryError("editable Python dependencies are unsupported.")
        if name in packages:
            if packages[name] != version:
                raise RuntimeInventoryError("multiple locked versions of one Python distribution are unsupported.")
            raise RuntimeInventoryError("Python dependency lockfile contains a duplicate normalized package name.")
        packages[name] = version
    return packages


def parse_poetry_lock(raw: str | bytes) -> dict[str, str]:
    """Parse Poetry lock v1/v2 package tables into normalized name/version pairs."""
    document = _decode_document(raw)
    metadata = document.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise RuntimeInventoryError("Poetry lock metadata must be a table.")
    lock_version = metadata.get("lock-version")
    if lock_version not in {"1.1", "2.0", "2.1"}:
        raise RuntimeInventoryError("unsupported Poetry lockfile version.")
    return _parse_packages(document.get("package"))


def parse_uv_lock(raw: str | bytes) -> dict[str, str]:
    """Parse uv lock package tables into normalized name/version pairs."""
    document = _decode_document(raw)
    lock_version = document.get("version")
    if type(lock_version) is not int or lock_version not in {1, 2, 3}:
        raise RuntimeInventoryError("unsupported uv lockfile version.")
    return _parse_packages(document.get("package"))


def parse_python_lockfile(raw: str | bytes, format: str) -> dict[str, str]:
    """Dispatch only explicitly supported lockfile formats."""
    if format == "poetry":
        return parse_poetry_lock(raw)
    if format == "uv":
        return parse_uv_lock(raw)
    raise RuntimeInventoryError("unsupported Python dependency lockfile format.")
