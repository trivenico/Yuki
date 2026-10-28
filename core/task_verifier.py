"""Independent, bounded verification helpers for workspace file artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path

_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024


def verify_workspace_file(
    workspace: str | Path, relative_path: str, *, expected_text: str | None = None
) -> dict[str, str | int | bool]:
    """Verify that a regular, non-symlink file exists under the workspace and hash it.

    This proves artifact presence and identity only, not that its contents satisfy
    the user's goal. The caller must display that limitation with the evidence.
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Provide a workspace-relative file path.")
    if expected_text is not None:
        if not isinstance(expected_text, str) or not expected_text:
            raise ValueError("Expected text must be a non-empty string.")
        if len(expected_text) > 4000:
            raise ValueError("Expected text must be at most 4000 characters.")
    supplied = Path(relative_path)
    if supplied.is_absolute():
        raise ValueError("Absolute paths are not accepted; use a workspace-relative path.")
    root = Path(workspace).resolve()
    candidate = root / supplied
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("File must exist inside the configured workspace.") from exc

    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("Symbolic links are not accepted for verification.")
        parent = cursor.parent
        if parent == cursor:
            raise ValueError("File must exist inside the configured workspace.")
        cursor = parent
    if not resolved.is_file():
        raise ValueError("Verification target must be a regular file.")
    size = resolved.stat().st_size
    if size > _MAX_ARTIFACT_BYTES:
        raise ValueError("Verification target exceeds the 50 MiB limit.")

    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    result: dict[str, str | int | bool] = {
        "path": resolved.relative_to(root).as_posix(),
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "scope": "file_presence_and_identity_only",
    }
    if expected_text is not None:
        if size > 1024 * 1024:
            raise ValueError("Text-content verification is limited to files of 1 MiB or less.")
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Text-content verification requires a UTF-8 text file.") from exc
        result["content_match"] = expected_text in content
        result["scope"] = "file_identity_and_literal_text_match"
    return result
