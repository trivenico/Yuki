"""Read-only Python runtime/package inventory from a live Linux container root.

This observer never executes a container binary, imports task packages, invokes
a shell, or reads the task workspace. It locates a host PID through the exact
container ID in procfs cgroup membership, then traverses the container root
through bounded, fixed metadata locations with directory-relative O_NOFOLLOW operations.
"""
from __future__ import annotations

import os
import re
import stat
from typing import Mapping

from core.host_runtime_inventory import RuntimeInventoryError

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

    def _find_container_root(self, container_id: str) -> int:
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
                        return os.open(
                            "root",
                            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=pid_fd,
                        )
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
        root_fd = self._find_container_root(container_id)
        try:
            runtimes, packages = _walk_python_metadata(root_fd)
        finally:
            os.close(root_fd)
        if not runtimes:
            raise RuntimeInventoryError("no supported Python runtime metadata found in the live container.")
        return {"runtime_versions": runtimes, "installed_dependencies": packages}


def build_observer():
    """Build the procfs-only observer; no Docker socket is required."""
    proc_root = os.environ.get("YUKI_HOST_INVENTORY_PROC_ROOT", "/proc")
    return LinuxRuntimeFilesystemObserver(proc_root=proc_root)
