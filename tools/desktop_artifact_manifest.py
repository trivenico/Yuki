"""Create portable SHA-256 evidence for an unsigned Yuki desktop bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import sys
from pathlib import Path


def build_manifest(bundle: Path, *, commit: str = "", ref: str = "") -> dict:
    root = bundle.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("bundle path must be a directory")
    files = []
    symlinks = []
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(current) / d).is_symlink())
        for name in sorted(names):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                symlinks.append({
                    "path": relative,
                    "target_kind": "absolute" if os.path.isabs(target) else "relative",
                })
                continue
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
            files.append({
                "path": relative,
                "size": size,
                "mode": stat.S_IMODE(path.stat().st_mode),
                "sha256": digest.hexdigest(),
            })
    if not files:
        raise ValueError("bundle contains no regular files")
    return {
        "schema_version": 1,
        "product": "Yuki Desktop",
        "artifact_kind": "unsigned-developer-bundle",
        "source_commit": commit or None,
        "source_ref": ref or None,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "files": files,
        "symlinks": symlinks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    args = parser.parse_args()
    bundle = args.bundle
    if not bundle.exists() and Path(str(bundle) + ".app").is_dir():
        bundle = Path(str(bundle) + ".app")
    manifest = build_manifest(bundle, commit=args.commit, ref=args.ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Manifest created for {len(manifest['files'])} files: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
