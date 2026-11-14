"""Portable Yuki application entrypoint for an authenticated bundle.
The host bootstrap must verify and stage the complete bundle before launching
the embedded interpreter with this file as its script, e.g.
  runtime/bin/python -I portable_entry.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Callable

_DLL_DIRECTORY_HANDLES: list[object] = []
_REGISTERED_DLL_DIRECTORIES: set[str] = set()


class PortableEntryError(RuntimeError):
    """Raised when the portable application layout is incomplete or unsafe."""


def _bundle_root(bundle_root: str | Path) -> Path:
    candidate = Path(bundle_root)
    if candidate.is_symlink() or not candidate.is_dir():
        raise PortableEntryError("Portable bundle root must be an existing non-symlink directory.")
    return candidate.resolve(strict=True)


def configure_bundle_paths(bundle_root: str | Path, path_list: list[str] | None = None) -> list[str]:
    root = _bundle_root(bundle_root)
    packages_candidate = root / "site-packages"
    if packages_candidate.is_symlink() or not packages_candidate.is_dir():
        raise PortableEntryError("Portable site-packages must be an existing non-symlink directory.")
    packages = packages_candidate.resolve(strict=True)
    try:
        packages.relative_to(root)
    except ValueError as exc:
        raise PortableEntryError("Portable site-packages must remain inside the bundle.") from exc
    target = sys.path if path_list is None else path_list
    for item in (str(root), str(packages)):
        while item in target:
            target.remove(item)
    target[0:0] = [str(packages), str(root)]
    return target


def configure_native_libraries(
    bundle_root: str | Path,
    *,
    platform_name: str | None = None,
    add_dll_directory: Callable[[str], object] | None = None,
) -> list[str]:
    """Register documented bundle-local DLL directories on Windows only."""
    active_platform = os.name if platform_name is None else platform_name
    if active_platform != "nt":
        return []
    root = _bundle_root(bundle_root)
    runtime = root / "runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise PortableEntryError("Portable runtime must be an existing non-symlink directory.")
    candidates = [runtime]
    native_libs = root / "native-libs"
    if native_libs.exists() or native_libs.is_symlink():
        if native_libs.is_symlink() or not native_libs.is_dir():
            raise PortableEntryError("Portable native-libs must be a non-symlink directory.")
        candidates.append(native_libs)
    registrar = add_dll_directory
    if registrar is None:
        registrar = getattr(os, "add_dll_directory", None)
    if registrar is None:
        raise PortableEntryError("This Python runtime does not support secure DLL directory registration.")
    registered: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PortableEntryError("Native library directories must remain inside the bundle.") from exc
        key = str(resolved)
        if key in _REGISTERED_DLL_DIRECTORIES:
            continue
        try:
            handle = registrar(key)
        except OSError as exc:
            raise PortableEntryError(f"Unable to register bundled DLL directory: {candidate.name}") from exc
        _DLL_DIRECTORY_HANDLES.append(handle)
        _REGISTERED_DLL_DIRECTORIES.add(key)
        registered.append(key)
    return registered


def main() -> int:
    script = Path(__file__)
    if script.is_symlink():
        raise PortableEntryError("Portable entrypoint must not be a symbolic link.")
    bundle_root = script.resolve(strict=True).parent
    configure_bundle_paths(bundle_root)
    configure_native_libraries(bundle_root)
    from main import main as yuki_main
    result = yuki_main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
