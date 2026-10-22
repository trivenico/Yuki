"""Stage validated self-improvement proposals into isolated review workspaces.

Staging never edits the active checkout, runs generated code, or deploys. A later
supervised runner may execute tests against the staged directory in a sandbox.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid

from core.self_improvement import ChangeProposal


class ProposalStagingError(RuntimeError):
    """A proposal could not be staged safely."""


def stage_change_proposal(*, proposal: ChangeProposal, workspace: str | os.PathLike[str]) -> Path:
    """Write a validated proposal into a new, unique child of .yuki-improvements."""
    root = Path(workspace).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ProposalStagingError("Configured workspace must be an existing non-symlink directory.")
    try:
        root = root.resolve(strict=True)
        parent = root / ".yuki-improvements"
        if parent.is_symlink():
            raise ProposalStagingError("Improvement staging directory cannot be a symlink.")
        parent.mkdir(mode=0o700, exist_ok=True)
        if parent.is_symlink() or not parent.is_dir() or parent.resolve(strict=True).parent != root:
            raise ProposalStagingError("Improvement staging directory failed containment checks.")
        target = parent / ("yuki-improvement-" + uuid.uuid4().hex)
        target.mkdir(mode=0o700, exist_ok=False)
    except ProposalStagingError:
        raise
    except OSError as exc:
        raise ProposalStagingError("Could not create a safe staging workspace.") from exc

    try:
        for item in proposal.files:
            # The proposal validator normalizes and rejects traversal before staging.
            relative = Path(item.path)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise ProposalStagingError("Proposal contains an unsafe relative path.")
            current = target
            for part in relative.parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise ProposalStagingError("Symlink in proposed destination path is forbidden.")
                current.mkdir(mode=0o700, exist_ok=True)
                if not current.is_dir():
                    raise ProposalStagingError("Proposed destination parent is not a directory.")
            destination = target / relative
            if destination.is_symlink() or destination.exists():
                raise ProposalStagingError("Staging never overwrites an existing file.")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(destination, flags, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                    stream.write(item.content)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
        metadata = target / "PROPOSAL.txt"
        with metadata.open("x", encoding="utf-8") as stream:
            stream.write(f"Title: {proposal.title}\nRationale: {proposal.rationale}\nSHA-256: {proposal.sha256}\nStatus: staged for review only\n")
            stream.flush()
            os.fsync(stream.fileno())
        return target
    except Exception as exc:
        try:
            shutil.rmtree(target)
        except OSError:
            pass
        if isinstance(exc, ProposalStagingError):
            raise
        raise ProposalStagingError("Proposal staging failed; partial workspace was removed where possible.") from exc
