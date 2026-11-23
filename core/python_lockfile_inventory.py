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
    if source_type is not None and type(source_type) is not str:
        raise RuntimeInventoryError("Python dependency lockfile source type is malformed.")
    if source_type not in {None, "pypi", "legacy"}:
        raise RuntimeInventoryError("custom, VCS, path, file and workspace Python sources are unsupported.")

    # Poetry's legacy source representation and uv's registry representation
    # are accepted only for the canonical public PyPI endpoint.
    registry = source.get("registry", source.get("url"))
    if registry is not None and (
        type(registry) is not str
        or registry.rstrip("/") != "https://pypi.org/simple"
    ):
        raise RuntimeInventoryError("Python dependency lockfile uses an untrusted package registry.")
    if "registry" in source and "url" in source:
        raise RuntimeInventoryError("Python dependency lockfile source metadata is ambiguous.")
    allowed_keys = {"type", "registry"} if "registry" in source else {"type", "url", "reference"}
    if any(key not in allowed_keys for key in source):
        raise RuntimeInventoryError("non-registry Python dependency source is unsupported.")
    reference = source.get("reference")
    if reference is not None and reference != "pypi":
        raise RuntimeInventoryError("Python dependency lockfile source reference is untrusted.")
    if source_type == "legacy" and registry is None:
        raise RuntimeInventoryError("Poetry legacy source has no verified registry URL.")


def _parse_packages(entries: object) -> dict[str, str]:
    if not isinstance(entries, list) or len(entries) > _MAX_PACKAGES:
        raise RuntimeInventoryError("Python dependency lockfile package list is missing or exceeds its entry ceiling.")
    packages: dict[str, str] = {}
    for package in entries:
        if not isinstance(package, Mapping):
            raise RuntimeInventoryError("Python dependency lockfile package entry must be a table.")
        source = package.get("source")
        # uv records the current project itself as an editable "." package.
        # It is the workspace root, not an installed dependency; exclude only
        # this exact root marker. Other editable/path sources remain rejected.
        if (isinstance(source, Mapping) and set(source) == {"editable"}
                and source.get("editable") == "."):
            continue
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
    if type(lock_version) is not str or lock_version not in {"1.1", "2.0", "2.1"}:
        raise RuntimeInventoryError("unsupported Poetry lockfile version.")
    return _parse_packages(document.get("package"))


def parse_uv_lock(raw: str | bytes) -> dict[str, str]:
    """Parse uv lock package tables into normalized name/version pairs."""
    document = _decode_document(raw)
    lock_version = document.get("version")
    if type(lock_version) is not int or lock_version not in {1, 2, 3}:
        raise RuntimeInventoryError("unsupported uv lockfile version.")
    return _parse_packages(document.get("package"))



_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]{0,127})(?:\[[A-Za-z0-9_,.-]+\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]{0,63})$"
)


def parse_requirements_lock(raw: str | bytes) -> dict[str, str]:
    """Parse a deliberately strict, fully pinned requirements file.

    Includes, options, URLs, editable installs, environment markers, ranges,
    and unhashed/non-exact declarations are not accepted as lock evidence.
    """
    if type(raw) is bytes:
        if len(raw) > _MAX_BYTES:
            raise RuntimeInventoryError("requirements file exceeds its byte ceiling.")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise RuntimeInventoryError("requirements file is not valid UTF-8.") from exc
    elif type(raw) is str and len(raw.encode("utf-8")) <= _MAX_BYTES:
        text = raw
    else:
        raise RuntimeInventoryError("requirements file input is invalid or exceeds its byte ceiling.")
    packages: dict[str, str] = {}
    lines = text.splitlines()
    if len(lines) > _MAX_PACKAGES * 4:
        raise RuntimeInventoryError("requirements file line count exceeds its ceiling.")
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Only whole-line comments are accepted: inline syntax can carry
        # markers or option semantics and is intentionally not interpreted.
        match = _REQUIREMENT.fullmatch(stripped)
        if not match:
            raise RuntimeInventoryError("requirements file contains a non-exact or unsupported dependency declaration.")
        name = _normalize_name(match.group(1))
        version = _validate_version(match.group(2))
        if name in packages:
            raise RuntimeInventoryError("requirements file contains duplicate normalized package identities.")
        packages[name] = version
        if len(packages) > _MAX_PACKAGES:
            raise RuntimeInventoryError("requirements file package count exceeds its ceiling.")
    if not packages:
        raise RuntimeInventoryError("requirements file contains no pinned dependencies.")
    return packages


def parse_python_lockfile(raw: str | bytes, format: str) -> dict[str, str]:
    """Dispatch only explicitly supported lockfile formats."""
    if format == "poetry":
        return parse_poetry_lock(raw)
    if format == "uv":
        return parse_uv_lock(raw)
    raise RuntimeInventoryError("unsupported Python dependency lockfile format.")


_PROJECT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{0,127})(?:\[[A-Za-z0-9_,.-]+\])?(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[^;\s]+)?(?:\s*;\s*.+)?$")
_PROJECT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,63}$")


def parse_pyproject_metadata(raw: str | bytes) -> dict[str, object]:
    """Validate PEP 621 project identity and classify declared dependencies.

    Returns project identity and a bounded dependency-role map. This metadata
    does not prove installed state or resolve version constraints.
    """
    document = _decode_document(raw)
    project = document.get("project")
    if not isinstance(project, Mapping):
        raise RuntimeInventoryError("pyproject.toml must contain a PEP 621 [project] table.")
    name = _normalize_name(project.get("name"))
    version = project.get("version")
    if version is not None and (type(version) is not str or not _PROJECT_VERSION.fullmatch(version)):
        raise RuntimeInventoryError("pyproject.toml contains an invalid project version.")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(type(item) is not str for item in dynamic):
        raise RuntimeInventoryError("pyproject.toml dynamic metadata declaration is malformed.")
    if "name" in dynamic or ("name" not in project and "name" not in dynamic):
        raise RuntimeInventoryError("pyproject.toml project name must be statically declared.")
    if "version" in dynamic and version is not None:
        raise RuntimeInventoryError("pyproject.toml version cannot be both static and dynamic.")

    roles: dict[str, set[str]] = {}

    def add_requirements(values: object, role: str) -> None:
        if not isinstance(values, list) or len(values) > _MAX_PACKAGES:
            raise RuntimeInventoryError("pyproject.toml dependency list is malformed or exceeds its ceiling.")
        for requirement in values:
            if type(requirement) is not str or len(requirement) > 512:
                raise RuntimeInventoryError("pyproject.toml dependency declaration is invalid.")
            match = _PROJECT_NAME.fullmatch(requirement.strip())
            if not match:
                raise RuntimeInventoryError("pyproject.toml contains an unsupported dependency declaration.")
            dependency_name = _normalize_name(match.group(1))
            roles.setdefault(dependency_name, set()).add(role)
            if len(roles) > _MAX_PACKAGES:
                raise RuntimeInventoryError("pyproject.toml dependency count exceeds its ceiling.")

    dependencies = project.get("dependencies", [])
    add_requirements(dependencies, "runtime")
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, Mapping) or len(optional) > 256:
        raise RuntimeInventoryError("pyproject.toml optional dependency groups are malformed.")
    for extra, values in optional.items():
        if type(extra) is not str or not _NAME.fullmatch(extra):
            raise RuntimeInventoryError("pyproject.toml contains an invalid optional dependency group.")
        add_requirements(values, "optional:" + extra.lower())

    return {
        "name": name,
        "version": version,
        "dependency_roles": {
            dependency: tuple(sorted(group_roles))
            for dependency, group_roles in sorted(roles.items())
        },
    }
