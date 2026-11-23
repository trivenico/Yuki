"""Read-only Python runtime/package inventory from a live Linux container root.

This observer never executes a container binary, imports task packages, invokes
a shell, or reads the task workspace. It opens the host PID's procfs root from
Docker's trusted inspect response and traverses only bounded, fixed metadata
locations with directory-relative O_NOFOLLOW operations.
"""
from __future__ import annotations

import os
import re
import stat
from typing import Mapping

from core.docker_engine_supervisor import DockerEngineAPI
from core.host_runtime_inventory import RuntimeInventoryError

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,255}@sha256:[0-9a-f]{64}$")
_PYTHON_DIR = re.compile(r"^python([0-9]+\.[0-9]+)$")
_VERSION = re.compile(r"^VERSION\s*=\s*([0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+-]*)\s*$")
_MAX_FILE = 256 * 1024
_MAX_ENTRIES = 20000
_MAX_DEPTH = 8
_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


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
            names = os.listdir(fd)
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
                if depth < _MAX_DEPTH:
                    visit(child, depth + 1, path)
            finally:
                os.close(child)

    visit(root_fd, 0, ())
    return runtimes, packages


def _find_python_version(python_lib_fd: int) -> str | None:
    try:
        names = os.listdir(python_lib_fd)
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
        names = os.listdir(python_lib_fd)
    except OSError:
        return
    for name in names:
        if not name.endswith(".dist-info") or "/" in name:
            continue
        info_fd = _open_dir_at(python_lib_fd, name)
        if info_fd is None:
            continue
        try:
            try:
                metadata = _read_at(info_fd, "METADATA")
            except (OSError, UnicodeError, RuntimeInventoryError):
                continue
            package_name = package_version = None
            for line in metadata.splitlines():
                if line.startswith("Name: ") and package_name is None:
                    package_name = line[6:].strip()
                elif line.startswith("Version: ") and package_version is None:
                    package_version = line[9:].strip()
                if package_name and package_version:
                    break
            if package_name and package_version:
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
    """Inspect Python runtime and dist-info metadata through a trusted host PID."""

    def __init__(self, *, docker_api: DockerEngineAPI, proc_root: str = "/proc") -> None:
        if not isinstance(docker_api, DockerEngineAPI):
            raise TypeError("a trusted Docker Engine API client is required.")
        if type(proc_root) is not str or not os.path.isabs(proc_root):
            raise ValueError("proc_root must be an absolute host path.")
        self.docker_api = docker_api
        self.proc_root = proc_root

    def __call__(
        self, task_id: str, worker_id: str, container_id: str,
        image_ref: str, spec_id: str,
    ) -> Mapping[str, Mapping[str, str]]:
        if (not all(type(value) is str and value and len(value) <= 512
                    for value in (task_id, worker_id, spec_id))
                or type(container_id) is not str or not _CONTAINER_ID.fullmatch(container_id)
                or type(image_ref) is not str or not _IMAGE_REF.fullmatch(image_ref)):
            raise RuntimeInventoryError("live runtime observer execution identity is invalid.")
        inspect = self.docker_api.get_json(f"/containers/yuki-{container_id[:32]}/json")
        if inspect.get("Id") != container_id:
            raise RuntimeInventoryError("Docker container identity changed before runtime observation.")
        config = inspect.get("Config")
        state = inspect.get("State")
        if (not isinstance(config, Mapping) or config.get("Image") != image_ref
                or not isinstance(state, Mapping) or state.get("Running") is not True
                or type(state.get("Pid")) is not int or state["Pid"] <= 0):
            raise RuntimeInventoryError("live container image or process identity is invalid.")
        pid = state["Pid"]
        root_path = os.path.join(self.proc_root, str(pid), "root")
        try:
            root_fd = os.open(root_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
        except OSError as exc:
            raise RuntimeInventoryError("live container root filesystem is unavailable.") from exc
        try:
            runtimes, packages = _walk_python_metadata(root_fd)
        finally:
            os.close(root_fd)
        if not runtimes:
            raise RuntimeInventoryError("no supported Python runtime metadata found in the live container.")
        return {"runtime_versions": runtimes, "installed_dependencies": packages}


def build_observer():
    """Build from service-managed Docker socket configuration."""
    socket_path = os.environ["YUKI_HOST_INVENTORY_DOCKER_SOCKET"]
    timeout_text = os.environ.get("YUKI_HOST_INVENTORY_DOCKER_TIMEOUT", "3")
    try:
        timeout = float(timeout_text)
    except ValueError as exc:
        raise RuntimeError("Docker timeout configuration is invalid.") from exc
    api = DockerEngineAPI(socket_path=socket_path, timeout_seconds=timeout)
    return LinuxRuntimeFilesystemObserver(docker_api=api)
