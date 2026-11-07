"""Create a consistent, checksummed backup of Yuki SQLite databases."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True, timeout=10)) as src:
        with closing(sqlite3.connect(destination, timeout=10)) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError("SQLite backup integrity check failed.")
    with destination.open("r+b") as handle:
        os.fsync(handle.fileno())


def _discover_databases(workspace: Path, output: Path) -> list[Path]:
    """Find workspace databases without following database-file symlinks."""
    databases = []
    for path in workspace.rglob("*"):
        if path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Refusing to back up a symbolic-link database: {path.relative_to(workspace)}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if workspace not in resolved.parents:
            raise ValueError("Database path resolves outside the live workspace.")
        if output == resolved or output in resolved.parents:
            continue
        databases.append(path)
    return sorted(databases)


def create_backup(workspace: Path, output: Path) -> Path:
    workspace = workspace.expanduser().resolve()
    output = output.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("Workspace directory does not exist.")
    incomplete_marker = workspace / ".yuki-restore-incomplete"
    if incomplete_marker.exists() or incomplete_marker.is_symlink():
        raise ValueError("Workspace has an incomplete restore; inspect or recover it before backup.")
    if output == workspace or workspace in output.parents:
        raise ValueError("Backup output must be outside the live workspace.")
    databases = _discover_databases(workspace, output)
    if not databases:
        raise ValueError("No SQLite databases were found in the workspace.")

    output.mkdir(parents=True, exist_ok=False)
    entries = []
    try:
        for source in databases:
            relative = source.relative_to(workspace)
            target = output / "databases" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=target.name + ".", suffix=".tmp", dir=target.parent,
                delete=False,
            ) as temporary:
                temp_path = Path(temporary.name)
            try:
                _backup_database(source, temp_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                temp_path.replace(target)
            finally:
                temp_path.unlink(missing_ok=True)
            entries.append({
                "path": relative.as_posix(),
                "backup_path": target.relative_to(output).as_posix(),
                "size_bytes": target.stat().st_size,
                "sha256": _sha256(target),
            })
        manifest = {
            "format": "yuki-sqlite-backup-v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "workspace_name": workspace.name,
            "database_count": len(entries),
            "databases": entries,
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest_path
    except Exception:
        import shutil
        shutil.rmtree(output, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="Yuki workspace (defaults to configured workspace)")
    parser.add_argument("--output", required=True, help="New, non-existing backup directory")
    args = parser.parse_args()
    if args.workspace:
        workspace = Path(args.workspace)
    else:
        from core.config import load_config
        workspace = Path(load_config()["workspace"])
    try:
        manifest = create_backup(workspace, Path(args.output))
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"Backup failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"Backup complete: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
