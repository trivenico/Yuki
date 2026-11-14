"""Portable Yuki application entrypoint for an authenticated bundle.

The host bootstrap must verify and stage the complete bundle before launching
the embedded interpreter with this file as its script, e.g.
  runtime/bin/python -I portable_entry.py
"""
from __future__ import annotations

import sys
from pathlib import Path


class PortableEntryError(RuntimeError):
    """Raised when the portable application layout is incomplete or unsafe."""


def configure_bundle_paths(bundle_root: str | Path, path_list: list[str] | None = None) -> list[str]:
    root_candidate = Path(bundle_root)
    if root_candidate.is_symlink() or not root_candidate.is_dir():
        raise PortableEntryError("Portable bundle root must be an existing non-symlink directory.")
    root = root_candidate.resolve(strict=True)
    packages_candidate = root / "site-packages"
    if packages_candidate.is_symlink() or not packages_candidate.is_dir():
        raise PortableEntryError("Portable site-packages must be an existing non-symlink directory.")
    packages = packages_candidate.resolve(strict=True)
    try:
        packages.relative_to(root)
    except ValueError as exc:
        raise PortableEntryError("Portable site-packages must remain inside the bundle.") from exc

    target = sys.path if path_list is None else path_list
    # Put the locked application/dependency tree first; the bundle root follows
    # for explicitly bundled assets. Host/global site-packages are not added.
    for item in (str(root), str(packages)):
        while item in target:
            target.remove(item)
    target[0:0] = [str(packages), str(root)]
    return target


def main() -> int:
    script = Path(__file__)
    if script.is_symlink():
        raise PortableEntryError("Portable entrypoint must not be a symbolic link.")
    bundle_root = script.resolve(strict=True).parent
    configure_bundle_paths(bundle_root)
    from main import main as yuki_main
    result = yuki_main()
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
