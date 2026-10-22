"""Integrity verification for staged self-improvement candidates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class StagedIntegrityError(ValueError):
    """Staged candidate bytes differ from the validated proposal."""


def verify_staged_workspace(workspace: str | Path) -> str:
    root = Path(workspace)
    if root.is_symlink() or not root.is_dir():
        raise StagedIntegrityError("Candidate workspace must be a regular directory.")
    root = root.resolve(strict=True)
    manifest_path = root / "MANIFEST.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise StagedIntegrityError("Candidate manifest is missing.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StagedIntegrityError("Candidate manifest is unreadable.") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise StagedIntegrityError("Candidate manifest schema is invalid.")
    proposal_sha = manifest.get("proposal_sha256")
    files = manifest.get("files")
    if not isinstance(proposal_sha, str) or len(proposal_sha) != 64:
        raise StagedIntegrityError("Candidate proposal digest is invalid.")
    if not isinstance(files, list) or not files or len(files) > 20:
        raise StagedIntegrityError("Candidate file list is invalid.")
    seen = set()
    proposal_digest = hashlib.sha256()
    expected = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise StagedIntegrityError("Candidate manifest entry is invalid.")
        relative, expected_sha, expected_size = item["path"], item["sha256"], item["size_bytes"]
        if not isinstance(relative, str) or not relative or relative.startswith("/") or "\\" in relative or any(p in {"", ".", ".."} for p in relative.split("/")):
            raise StagedIntegrityError("Candidate contains an unsafe path.")
        if relative.casefold() in seen:
            raise StagedIntegrityError("Candidate contains duplicate paths.")
        seen.add(relative.casefold())
        path = root.joinpath(*relative.split("/"))
        cursor = root
        for part in relative.split("/"):
            cursor = cursor / part
            if cursor.is_symlink():
                raise StagedIntegrityError("Symlinks are forbidden in candidate content.")
        if not path.is_file():
            raise StagedIntegrityError("Candidate file is missing.")
        raw = path.read_bytes()
        if not isinstance(expected_sha, str) or hashlib.sha256(raw).hexdigest() != expected_sha or type(expected_size) is not int or len(raw) != expected_size:
            raise StagedIntegrityError("Candidate file differs from its manifest.")
        expected.add(relative)
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise StagedIntegrityError("Symlinks are forbidden in candidate content.")
        if path.is_file() and path.name not in {"MANIFEST.json", "PROPOSAL.txt"}:
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise StagedIntegrityError("Candidate has unexpected or missing files.")
    for item in sorted(files, key=lambda value: value["path"].casefold()):
        proposal_digest.update(item["path"].encode("utf-8"))
        proposal_digest.update(b"\\0")
        proposal_digest.update((root / item["path"]).read_bytes())
        proposal_digest.update(b"\\0")
    if proposal_digest.hexdigest() != proposal_sha.lower():
        raise StagedIntegrityError("Candidate proposal digest does not match.")
    return proposal_sha.lower()
