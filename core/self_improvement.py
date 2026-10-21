"""Guarded self-improvement proposal validation.

This module deliberately does not apply patches, execute generated code, commit,
or deploy. It validates a proposed change and returns a reviewable record so a
separate, explicitly authorized workflow can test it in an isolated branch.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from pathlib import PurePosixPath
from typing import Iterable


PROTECTED_PREFIXES = (
    "core/permissions.py",
    "core/agent.py",
    "core/task_agent.py",
    "core/self_improvement.py",
    "core/readiness.py",
    ".github/workflows/",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "Dockerfile",
    "docker-compose.yml",
)
MAX_FILES = 20
MAX_FILE_BYTES = 256_000
MAX_TOTAL_BYTES = 1_000_000
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class ProposedFile:
    path: str
    content: str


@dataclass(frozen=True)
class ChangeProposal:
    title: str
    rationale: str
    files: tuple[ProposedFile, ...]
    requires_human_approval: bool
    status: str
    sha256: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["files"] = [asdict(item) for item in self.files]
        return data


class ProposalRejected(ValueError):
    """A generated change failed validation and must not be applied."""


def _validate_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 240:
        raise ProposalRejected("Each changed file must have a valid relative path.")
    if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ProposalRejected("Absolute paths and platform-specific separators are forbidden.")
    # Validate raw segments before PurePosixPath can normalize away "." or
    # repeated separators. These forms are ambiguous across filesystems.
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ProposalRejected("Traversal, empty, and dot path segments are forbidden.")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ProposalRejected("Control characters are forbidden in paths.")
    for part in raw_parts:
        if part.endswith((" ", ".")) or ":" in part:
            raise ProposalRejected("Windows path aliases and alternate data streams are forbidden.")
        device_name = part.split(".", 1)[0].upper()
        if device_name in _WINDOWS_RESERVED_NAMES:
            raise ProposalRejected("Reserved Windows device names are forbidden.")
    path = PurePosixPath(raw)
    if path.parts[0].casefold() in {".git", ".hg"}:
        raise ProposalRejected("Version-control internals cannot be modified.")
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
        raise ProposalRejected("Secret and private-key file types cannot be changed.")
    return path.as_posix()


def _needs_approval(path: str) -> bool:
    normalized = path.casefold()
    return any(
        normalized == protected.casefold()
        or (protected.endswith("/") and normalized.startswith(protected.casefold()))
        for protected in PROTECTED_PREFIXES
    )


def validate_change_proposal(
    *,
    title: str,
    rationale: str,
    files: Iterable[ProposedFile],
) -> ChangeProposal:
    """Validate a bounded patch proposal without writing to the filesystem."""
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ProposalRejected("Proposal title must be between 1 and 160 characters.")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale.strip()) > 4000:
        raise ProposalRejected("Proposal rationale must be between 1 and 4000 characters.")

    try:
        items = tuple(files)
    except TypeError as exc:
        raise ProposalRejected("Files must be an iterable of proposed files.") from exc
    if not items or len(items) > MAX_FILES:
        raise ProposalRejected(f"A proposal must change between 1 and {MAX_FILES} files.")

    seen: set[str] = set()
    total_bytes = 0
    normalized_items: list[ProposedFile] = []
    requires_approval = False
    for item in items:
        if not isinstance(item, ProposedFile):
            raise ProposalRejected("Every file must be a ProposedFile instance.")
        path = _validate_path(item.path)
        key = path.casefold()
        if key in seen:
            raise ProposalRejected("Duplicate paths are forbidden, including case-only duplicates.")
        seen.add(key)
        if not isinstance(item.content, str):
            raise ProposalRejected("File content must be text.")
        encoded = item.content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ProposalRejected(f"Proposed file exceeds the {MAX_FILE_BYTES}-byte limit.")
        if "\x00" in item.content:
            raise ProposalRejected("Binary content is not accepted.")
        total_bytes += len(encoded)
        if total_bytes > MAX_TOTAL_BYTES:
            raise ProposalRejected("Proposal exceeds the total content-size limit.")
        requires_approval = requires_approval or _needs_approval(path)
        normalized_items.append(ProposedFile(path=path, content=item.content))

    digest = hashlib.sha256()
    for item in sorted(normalized_items, key=lambda value: value.path.casefold()):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")

    return ChangeProposal(
        title=title.strip(),
        rationale=rationale.strip(),
        files=tuple(normalized_items),
        requires_human_approval=requires_approval,
        status="awaiting_human_approval" if requires_approval else "proposal_only",
        sha256=digest.hexdigest(),
    )
