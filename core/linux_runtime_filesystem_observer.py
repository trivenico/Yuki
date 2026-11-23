"""Read-only Python runtime/package inventory from a live Linux container root.

This observer never executes a container binary, imports task packages, invokes
a shell, or reads the task workspace. It locates a host PID through the exact
container ID in procfs cgroup membership, then traverses the container root
through bounded, fixed metadata locations with directory-relative O_NOFOLLOW operations.
"""
from __future__ import annotations

import json
import os
import re
import stat
from typing import Mapping

from core.host_runtime_inventory import RuntimeInventoryError
from core.linux_container_mount_provenance import (
    parse_container_mountinfo,
    verify_image_owned_application_root,
)
from core.npm_package_lock_inventory import parse_npm_package_lock
from core.python_lockfile_inventory import (
    parse_poetry_lock, parse_requirements_lock, parse_uv_lock,
)

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,255}@sha256:[0-9a-f]{64}$")
_PYTHON_DIR = re.compile(r"^python([0-9]+\.[0-9]+)$")
_VERSION = re.compile(r"^VERSION\s*=\s*([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*)\s*$")
_MAX_FILE = 256 * 1024
_MAX_ENTRIES = 20000
_MAX_DEPTH = 8
_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _bounded_names(directory_fd: int, ceiling: int = _MAX_ENTRIES) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > ceiling:
                    raise RuntimeInventoryError("runtime metadata directory entry ceiling exceeded.")
    except OSError as exc:
        raise RuntimeInventoryError("runtime metadata directory is unreadable.") from exc
    return names


def _read_at(directory_fd: int, name: str, limit: int = _MAX_FILE) -> str:
    fd = os.open(name, _FLAGS, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise RuntimeInventoryError("runtime metadata file is not regular or exceeds its size limit.")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > limit:
            raise RuntimeInventoryError("runtime metadata file exceeds its read limit.")
        return raw.decode("utf-8", errors="strict")
    finally:
        os.close(fd)


def _open_dir_at(directory_fd: int, name: str) -> int | None:
    try:
        fd = os.open(name, _FLAGS | getattr(os, "O_DIRECTORY", 0), dir_fd=directory_fd)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return fd


def _read_debian_packages(root_fd: int) -> dict[str, str]:
    """Read installed Debian packages from the container's dpkg status database."""
    opened: list[int] = []
    current = os.dup(root_fd)
    opened.append(current)
    try:
        for component in ("var", "lib", "dpkg"):
            child = _open_dir_at(current, component)
            if child is None:
                try:
                    entry = os.stat(component, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    return {}
                except OSError as exc:
                    raise RuntimeInventoryError("Debian package database path cannot be inspected.") from exc
                if stat.S_ISLNK(entry.st_mode):
                    raise RuntimeInventoryError("Debian package database path contains a symlink.")
                if not stat.S_ISDIR(entry.st_mode):
                    return {}
                raise RuntimeInventoryError("Debian package database directory is inaccessible.")
            opened.append(child)
            current = child
        try:
            status = _read_at(current, "status", limit=2 * 1024 * 1024)
        except (OSError, UnicodeError, RuntimeInventoryError) as exc:
            raise RuntimeInventoryError("Debian package status database is missing or unreadable.") from exc
    finally:
        for fd in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass

    packages: dict[str, str] = {}
    for paragraph in re.split(r"\n[ \t]*\n", status):
        fields: dict[str, str] = {}
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")) or ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key in {"Package", "Version", "Status"} and key not in fields:
                fields[key] = value.strip()
        if fields.get("Status") != "install ok installed":
            continue
        name, version = fields.get("Package"), fields.get("Version")
        if not name or not version:
            raise RuntimeInventoryError("installed Debian package record lacks a name or version.")
        normalized = re.sub(r"[^a-z0-9+.-]+", "-", name.lower())
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", normalized):
            raise RuntimeInventoryError("Debian package database contains an invalid package name.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+:~_-]{0,63}", version):
            raise RuntimeInventoryError("Debian package database contains an invalid package version.")
        key = "deb:" + normalized
        if key in packages and packages[key] != version:
            raise RuntimeInventoryError("Debian package database contains conflicting installed versions.")
        packages[key] = version
        if len(packages) > 4096:
            raise RuntimeInventoryError("Debian package inventory exceeds its item ceiling.")
    return packages


_NODE_VERSION_MACRO = re.compile(r"^#define NODE_(MAJOR|MINOR|PATCH)_VERSION ([0-9]+)$")
_NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]{0,63}/)?[a-z0-9][a-z0-9._~-]{0,127}$")
_NPM_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")


def _open_path_at(root_fd: int, parts: tuple[str, ...]) -> int | None:
    current = os.dup(root_fd)
    try:
        for part in parts:
            child = _open_dir_at(current, part)
            if child is None:
                try:
                    entry = os.stat(part, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    return None
                except OSError as exc:
                    raise RuntimeInventoryError("runtime metadata path cannot be inspected.") from exc
                if stat.S_ISLNK(entry.st_mode):
                    raise RuntimeInventoryError("symlinked runtime metadata path is not trusted.")
                if stat.S_ISDIR(entry.st_mode):
                    raise RuntimeInventoryError("runtime metadata directory is inaccessible.")
                return None
            os.close(current)
            current = child
        result = current
        current = -1
        return result
    finally:
        if current >= 0:
            os.close(current)



def _open_optional_dir_at(directory_fd: int, name: str) -> int | None:
    child = _open_dir_at(directory_fd, name)
    if child is not None:
        return child
    try:
        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeInventoryError("runtime package directory cannot be inspected.") from exc
    if stat.S_ISLNK(entry.st_mode):
        raise RuntimeInventoryError("symlinked runtime package directory is not trusted.")
    if stat.S_ISDIR(entry.st_mode):
        raise RuntimeInventoryError("runtime package directory is inaccessible.")
    return None

def _read_node_runtime_version(root_fd: int) -> str | None:
    for path in (("usr", "include", "node", "node_version.h"),
                 ("usr", "local", "include", "node", "node_version.h")):
        parent = _open_path_at(root_fd, path[:-1])
        if parent is None:
            continue
        try:
            try:
                header = _read_at(parent, path[-1], limit=32 * 1024)
            except (OSError, UnicodeError, RuntimeInventoryError) as exc:
                raise RuntimeInventoryError("Node.js version header is unreadable.") from exc
        finally:
            os.close(parent)
        parts: dict[str, str] = {}
        for line in header.splitlines():
            match = _NODE_VERSION_MACRO.fullmatch(line.strip())
            if match:
                parts[match.group(1)] = match.group(2)
        if set(parts) == {"MAJOR", "MINOR", "PATCH"}:
            return ".".join(parts[key] for key in ("MAJOR", "MINOR", "PATCH"))
        raise RuntimeInventoryError("Node.js version header is incomplete or malformed.")
    return None


def _json_unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _collect_npm_package(package_fd: int, packages: dict[str, str], *, location: str) -> None:
    try:
        raw = _read_at(package_fd, "package.json", limit=64 * 1024)
        metadata = json.loads(raw, object_pairs_hook=_json_unique_object)
    except (OSError, UnicodeError, RuntimeInventoryError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeInventoryError("global npm package metadata is missing, malformed or unreadable.") from exc
    if not isinstance(metadata, dict):
        raise RuntimeInventoryError("npm package metadata root must be an object.")
    name, version = metadata.get("name"), metadata.get("version")
    if (type(name) is not str or type(version) is not str
            or not _NPM_NAME.fullmatch(name) or not _NPM_VERSION.fullmatch(version)):
        raise RuntimeInventoryError("npm package metadata has an invalid name or version.")
    # Preserve every installed instance. A name-only key loses valid nested
    # dependency versions; the image-owned install path is part of its identity.
    path_parts = location.split("/")
    expected_path_name = "/".join(path_parts[-2:]) if len(path_parts) >= 2 and path_parts[-2].startswith("@") else path_parts[-1]
    if expected_path_name.lower() != name.lower():
        raise RuntimeInventoryError("npm package path does not match its declared package name.")
    key = "npm:" + name.lower() + "#" + location
    if len(key) > 128:
        raise RuntimeInventoryError("npm package identity exceeds its path ceiling.")
    if key in packages:
        raise RuntimeInventoryError("duplicate npm package install path found.")
    packages[key] = version
    if len(packages) > 4096:
        raise RuntimeInventoryError("runtime package inventory exceeds its item ceiling.")


def _read_global_npm_packages(root_fd: int, application_root_fd: int | None = None) -> dict[str, str]:
    """Read global npm manifests and nested node_modules with path identities."""
    packages: dict[str, str] = {}
    visited = 0

    def scan_modules(modules_fd: int, depth: int, prefix: str) -> None:
        nonlocal visited
        if depth > 6:
            raise RuntimeInventoryError("nested npm package depth ceiling exceeded.")
        for name in _bounded_names(modules_fd):
            visited += 1
            if visited > _MAX_ENTRIES:
                raise RuntimeInventoryError("npm package entry ceiling exceeded.")
            if name in {".bin", ".cache"}:
                continue
            if name.startswith("@"):
                if not re.fullmatch(r"@[a-z0-9][a-z0-9._~-]{0,63}", name):
                    raise RuntimeInventoryError("npm scope name is invalid.")
                scope_fd = _open_dir_at(modules_fd, name)
                if scope_fd is None:
                    raise RuntimeInventoryError("npm scope directory is not a real directory.")
                try:
                    for package_name in _bounded_names(scope_fd):
                        visited += 1
                        if visited > _MAX_ENTRIES:
                            raise RuntimeInventoryError("npm package entry ceiling exceeded.")
                        package_fd = _open_dir_at(scope_fd, package_name)
                        if package_fd is None:
                            raise RuntimeInventoryError("scoped npm package directory is unsafe.")
                        try:
                            location = prefix + "/" + name + "/" + package_name
                            _collect_npm_package(package_fd, packages, location=location)
                            nested_fd = _open_optional_dir_at(package_fd, "node_modules")
                            if nested_fd is not None:
                                try:
                                    scan_modules(nested_fd, depth + 1, location + "/node_modules")
                                finally:
                                    os.close(nested_fd)
                        finally:
                            os.close(package_fd)
                finally:
                    os.close(scope_fd)
                continue
            package_fd = _open_dir_at(modules_fd, name)
            if package_fd is None:
                try:
                    entry = os.stat(name, dir_fd=modules_fd, follow_symlinks=False)
                except OSError as exc:
                    raise RuntimeInventoryError("global npm entry changed during inspection.") from exc
                if stat.S_ISLNK(entry.st_mode):
                    raise RuntimeInventoryError("symlinked global npm package is not trusted.")
                if stat.S_ISDIR(entry.st_mode):
                    raise RuntimeInventoryError("global npm package directory is inaccessible.")
                continue
            try:
                location = prefix + "/" + name
                _collect_npm_package(package_fd, packages, location=location)
                nested_fd = _open_optional_dir_at(package_fd, "node_modules")
                if nested_fd is not None:
                    try:
                        scan_modules(nested_fd, depth + 1, location + "/node_modules")
                    finally:
                        os.close(nested_fd)
            finally:
                os.close(package_fd)

    if application_root_fd is None:
        for path in (("usr", "lib", "node_modules"),
                     ("usr", "local", "lib", "node_modules")):
            modules_fd = _open_path_at(root_fd, path)
            if modules_fd is not None:
                try:
                    scan_modules(modules_fd, 0, "/".join(path))
                finally:
                    os.close(modules_fd)
    if application_root_fd is not None:
        modules_fd = _open_optional_dir_at(application_root_fd, "node_modules")
        if modules_fd is None:
            raise RuntimeInventoryError("verified application root has no readable node_modules directory.")
        try:
            scan_modules(modules_fd, 0, "app/node_modules")
        finally:
            os.close(modules_fd)
    return packages

def _walk_python_metadata(root_fd: int) -> tuple[dict[str, str], dict[str, str]]:
    runtimes: dict[str, str] = {}
    packages: dict[str, str] = {}
    visited = 0

    def visit(fd: int, depth: int, prefix: tuple[str, ...]) -> None:
        nonlocal visited
        if depth > _MAX_DEPTH:
            return
        try:
            names = _bounded_names(fd)
        except OSError:
            return
        for name in names:
            visited += 1
            if visited > _MAX_ENTRIES:
                raise RuntimeInventoryError("container runtime metadata entry ceiling exceeded.")
            if name in {".", ".."} or "/" in name or "\x00" in name:
                continue
            child = _open_dir_at(fd, name)
            if child is None:
                continue
            try:
                path = prefix + (name,)
                match = _PYTHON_DIR.fullmatch(name)
                if match and path[-2:] in {("lib", name), ("lib64", name)}:
                    version = _find_python_version(child)
                    if version:
                        previous = runtimes.get("python")
                        if previous is not None and previous != version:
                            raise RuntimeInventoryError("multiple conflicting Python runtimes found in the live container.")
                        runtimes["python"] = version
                    _collect_dist_info(child, packages)
                    for site_name in ("site-packages", "dist-packages"):
                        site_fd = _open_dir_at(child, site_name)
                        if site_fd is not None:
                            try:
                                _collect_dist_info(site_fd, packages)
                            finally:
                                os.close(site_fd)
                if depth < _MAX_DEPTH:
                    visit(child, depth + 1, path)
            finally:
                os.close(child)

    visit(root_fd, 0, ())
    return runtimes, packages


def _find_python_version(python_lib_fd: int) -> str | None:
    try:
        names = _bounded_names(python_lib_fd)
    except OSError:
        return None
    for name in names:
        if not name.startswith("config-") or "/" in name:
            continue
        config_fd = _open_dir_at(python_lib_fd, name)
        if config_fd is None:
            continue
        try:
            try:
                makefile = _read_at(config_fd, "Makefile")
            except (OSError, UnicodeError, RuntimeInventoryError):
                continue
            for line in makefile.splitlines():
                match = _VERSION.fullmatch(line.strip())
                if match:
                    return match.group(1)
        finally:
            os.close(config_fd)
    return None


def _collect_dist_info(python_lib_fd: int, packages: dict[str, str]) -> None:
    try:
        names = _bounded_names(python_lib_fd)
    except OSError:
        return
    for name in names:
        if not name.endswith(".dist-info") or "/" in name:
            continue
        info_fd = _open_dir_at(python_lib_fd, name)
        if info_fd is None:
            # A .dist-info entry is an inventory claim. Never silently omit it
            # when it is not a real, readable directory.
            try:
                entry_info = os.stat(name, dir_fd=python_lib_fd, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeInventoryError("Python distribution metadata changed during inspection.") from exc
            if stat.S_ISLNK(entry_info.st_mode):
                raise RuntimeInventoryError("symlinked Python distribution metadata is not trusted.")
            raise RuntimeInventoryError("Python distribution metadata directory is not accessible.")
        try:
            try:
                metadata = _read_at(info_fd, "METADATA")
            except (OSError, UnicodeError, RuntimeInventoryError) as exc:
                raise RuntimeInventoryError("Python distribution metadata is missing or unreadable.") from exc
            package_name = package_version = None
            for line in metadata.splitlines():
                if line.startswith("Name: ") and package_name is None:
                    package_name = line[6:].strip()
                elif line.startswith("Version: ") and package_version is None:
                    package_version = line[9:].strip()
                if package_name and package_version:
                    break
            if not package_name or not package_version:
                raise RuntimeInventoryError("Python distribution metadata lacks a name or version.")
            normalized = re.sub(r"[-_.]+", "-", package_name).lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,127}", normalized):
                raise RuntimeInventoryError("live Python package metadata contains an invalid package name.")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,63}", package_version):
                raise RuntimeInventoryError("live Python package metadata contains an invalid version.")
            if normalized in packages and packages[normalized] != package_version:
                raise RuntimeInventoryError("conflicting installed versions found for a Python package.")
            packages[normalized] = package_version
            if len(packages) > 4096:
                raise RuntimeInventoryError("live Python package inventory exceeds its item ceiling.")
        finally:
            os.close(info_fd)


def _read_application_python_inventory(application_root_fd: int) -> dict[str, str]:
    """Reconcile one supported Python lock source with an app-owned venv."""
    lock_candidates = (
        ("poetry.lock", parse_poetry_lock),
        ("uv.lock", parse_uv_lock),
        ("requirements.txt", parse_requirements_lock),
    )
    present = []
    for filename, parser in lock_candidates:
        try:
            info = os.stat(filename, dir_fd=application_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeInventoryError("application Python lockfile cannot be inspected.") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RuntimeInventoryError("application Python lockfile must be a regular non-symlink file.")
        present.append((filename, parser))
    if not present:
        return {}
    if len(present) != 1:
        raise RuntimeInventoryError("application root contains multiple Python lock sources; provenance is ambiguous.")
    filename, parser = present[0]
    try:
        lock_raw = _read_at(application_root_fd, filename, limit=4 * 1024 * 1024)
        locked = parser(lock_raw)
    except (OSError, UnicodeError, RuntimeInventoryError) as exc:
        raise RuntimeInventoryError("application Python lockfile is missing, malformed or unreadable.") from exc

    environments = []
    for environment in (".venv", "venv"):
        env_fd = _open_optional_dir_at(application_root_fd, environment)
        if env_fd is None:
            continue
        try:
            for lib_name in ("lib", "lib64"):
                lib_fd = _open_optional_dir_at(env_fd, lib_name)
                if lib_fd is None:
                    continue
                try:
                    for python_dir in _bounded_names(lib_fd):
                        if not _PYTHON_DIR.fullmatch(python_dir):
                            continue
                        version_fd = _open_optional_dir_at(lib_fd, python_dir)
                        if version_fd is None:
                            raise RuntimeInventoryError("application virtualenv Python directory is unsafe.")
                        try:
                            site_fd = _open_optional_dir_at(version_fd, "site-packages")
                            if site_fd is not None:
                                environments.append(site_fd)
                        finally:
                            os.close(version_fd)
                finally:
                    os.close(lib_fd)
        finally:
            os.close(env_fd)
    if len(environments) != 1:
        for fd in environments:
            os.close(fd)
        raise RuntimeInventoryError("Python lockfile requires exactly one image-owned application virtualenv site-packages.")
    installed: dict[str, str] = {}
    try:
        _collect_dist_info(environments[0], installed)
    finally:
        os.close(environments[0])
    if installed != locked:
        raise RuntimeInventoryError("application Python lockfile and installed virtualenv inventory differ.")
    return installed


class LinuxRuntimeFilesystemObserver:
    """Inspect Python metadata through a process in the exact container cgroup.

    The container ID comes from the authenticated supervisor request. The
    observer discovers a host PID by matching that full ID in procfs cgroup
    membership; it does not need Docker socket access or execute container code.
    """

    def __init__(self, *, proc_root: str = "/proc", max_processes: int = 16384) -> None:
        if type(proc_root) is not str or not os.path.isabs(proc_root):
            raise ValueError("proc_root must be an absolute host path.")
        if type(max_processes) is not int or not 1 <= max_processes <= 65536:
            raise ValueError("max_processes must be between 1 and 65536.")
        self.proc_root = proc_root
        self.max_processes = max_processes

    def _find_container_root(self, container_id: str):
        token = re.compile(r"(?<![0-9a-f])" + re.escape(container_id) + r"(?![0-9a-f])")
        try:
            proc_fd = os.open(
                self.proc_root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as exc:
            raise RuntimeInventoryError("host procfs process inventory is unavailable.") from exc
        try:
            entries = _bounded_names(proc_fd, self.max_processes + 1)
            checked = 0
            for entry in entries:
                if not entry.isascii() or not entry.isdigit() or entry.startswith("0"):
                    continue
                checked += 1
                if checked > self.max_processes:
                    raise RuntimeInventoryError("host process scan exceeded its configured ceiling.")
                try:
                    # Pin the procfs process directory before reading cgroup
                    # membership and opening root; this prevents PID-reuse
                    # races between separate /proc/<pid>/ path lookups.
                    pid_fd = os.open(
                        entry,
                        _FLAGS | getattr(os, "O_DIRECTORY", 0),
                        dir_fd=proc_fd,
                    )
                except OSError:
                    continue
                try:
                    try:
                        cgroup_fd = os.open("cgroup", _FLAGS, dir_fd=pid_fd)
                        try:
                            info = os.fstat(cgroup_fd)
                            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                                continue
                            raw = os.read(cgroup_fd, 4097)
                        finally:
                            os.close(cgroup_fd)
                        if len(raw) > 4096:
                            continue
                        membership = raw.decode("ascii", errors="strict")
                    except (OSError, UnicodeError):
                        continue
                    if not any(token.search(line) for line in membership.splitlines()):
                        continue
                    try:
                        # /proc/<pid>/root is intentionally a procfs magic
                        # link; follow it only relative to the pinned PID fd.
                        mountinfo_fd = os.open("mountinfo", _FLAGS, dir_fd=pid_fd)
                        try:
                            mountinfo_stat = os.fstat(mountinfo_fd)
                            if not stat.S_ISREG(mountinfo_stat.st_mode) or mountinfo_stat.st_size > 1024 * 1024:
                                raise RuntimeInventoryError("container mountinfo is not a bounded regular procfs file.")
                            mountinfo_raw = os.read(mountinfo_fd, 1024 * 1024 + 1)
                            if len(mountinfo_raw) > 1024 * 1024:
                                raise RuntimeInventoryError("container mountinfo exceeds its byte ceiling.")
                        finally:
                            os.close(mountinfo_fd)
                        mounts = parse_container_mountinfo(mountinfo_raw)
                        root_fd = os.open(
                            "root",
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=pid_fd,
                        )
                        return root_fd, mounts
                    except OSError:
                        continue
                finally:
                    os.close(pid_fd)
        finally:
            os.close(proc_fd)
        raise RuntimeInventoryError("no readable host process is bound to the requested container cgroup.")

    def __call__(
        self, task_id: str, worker_id: str, container_id: str,
        image_ref: str, spec_id: str,
    ) -> Mapping[str, Mapping[str, str]]:
        if (not all(type(value) is str and value and len(value) <= 512
                    for value in (task_id, worker_id, spec_id))
                or type(container_id) is not str or not _CONTAINER_ID.fullmatch(container_id)
                or type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref)):
            raise RuntimeInventoryError("live runtime observer execution identity is invalid.")
        root_fd, mounts = self._find_container_root(container_id)
        try:
            runtimes, packages = _walk_python_metadata(root_fd)
            debian_packages = _read_debian_packages(root_fd)
            packages.update(debian_packages)
            node_version = _read_node_runtime_version(root_fd)
            if node_version:
                runtimes["nodejs"] = node_version
            application_root_fd = None
            configured_application_root = os.environ.get("YUKI_HOST_INVENTORY_APPLICATION_ROOT")
            if configured_application_root:
                verified_root = verify_image_owned_application_root(
                    configured_application_root, mounts,
                )
                components = tuple(part for part in verified_root.split("/") if part)
                application_root_fd = _open_path_at(root_fd, components)
                if application_root_fd is None:
                    raise RuntimeInventoryError("verified application root is missing or inaccessible.")
                try:
                    try:
                        package_lock_stat = os.stat(
                            "package-lock.json", dir_fd=application_root_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        package_lock_stat = None
                    except OSError as exc:
                        raise RuntimeInventoryError("application package-lock cannot be inspected.") from exc
                    if package_lock_stat is not None:
                        if stat.S_ISLNK(package_lock_stat.st_mode) or not stat.S_ISREG(package_lock_stat.st_mode):
                            raise RuntimeInventoryError("application package-lock must be a regular non-symlink file.")
                        try:
                            lock_raw = _read_at(application_root_fd, "package-lock.json", limit=4 * 1024 * 1024)
                        except (OSError, UnicodeError, RuntimeInventoryError) as exc:
                            raise RuntimeInventoryError("verified application package-lock is unreadable.") from exc
                        locked = parse_npm_package_lock(lock_raw)
                        app_packages = _read_global_npm_packages(application_root_fd, application_root_fd)
                        app_packages = {
                            key: value for key, value in app_packages.items()
                            if key.startswith("npm:") and "#app/node_modules/" in key
                        }
                        if app_packages != locked:
                            raise RuntimeInventoryError("application npm lockfile and installed node_modules inventory differ.")
                        packages.update(app_packages)
                    else:
                        untracked_node_modules = _open_optional_dir_at(application_root_fd, "node_modules")
                        if untracked_node_modules is not None:
                            os.close(untracked_node_modules)
                            raise RuntimeInventoryError("application node_modules exists without a package-lock.")
                    app_python_packages = _read_application_python_inventory(application_root_fd)
                    for name, version in app_python_packages.items():
                        if name in packages and packages[name] != version:
                            raise RuntimeInventoryError("application Python dependency conflicts with another installed inventory.")
                        packages[name] = version
                finally:
                    os.close(application_root_fd)
            packages.update(_read_global_npm_packages(root_fd))
        finally:
            os.close(root_fd)
        if not runtimes:
            raise RuntimeInventoryError("no supported runtime metadata found in the live container.")
        return {"runtime_versions": runtimes, "installed_dependencies": packages}


def build_observer():
    """Build the procfs-only observer; no Docker socket is required."""
    proc_root = os.environ.get("YUKI_HOST_INVENTORY_PROC_ROOT", "/proc")
    return LinuxRuntimeFilesystemObserver(proc_root=proc_root)
