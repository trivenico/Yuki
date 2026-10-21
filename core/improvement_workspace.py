"""Disposable workspace staging for validated self-improvement proposals.

This module stages reviewable files only. It never executes code, mutates the
repository checkout, commits, or deploys. A caller must run tests in a separate
sandbox and remove the workspace after the job.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from core.self_improvement import (
    ChangeProposal,
    ProposalRejected,
    validate_change_proposal,
)


class WorkspaceStagingError(RuntimeError):
    """A proposal could not be safely staged."""


def stage_change_proposal(
    proposal: ChangeProposal,
    *,
    parent_dir: str | os.PathLike[str],
    approved_sha256: str | None = None,
) -> Path:
    """Stage a validated proposal in a new private disposable directory.

    Protected paths require approval bound to the proposal's exact SHA-256.
    The workspace is newly created, mode 0700 where supported, and files are
    created exclusively so a pre-existing final-path symlink cannot be followed.
    """
    if not isinstance(proposal, ChangeProposal):
        raise WorkspaceStagingError("A validated change proposal is required.")

    try:
        verified = validate_change_proposal(
            title=proposal.title,
            rationale=proposal.rationale,
            files=proposal.files,
        )
    except (ProposalRejected, TypeError, AttributeError) as exc:
        raise WorkspaceStagingError("Proposal failed validation before staging.") from exc

    if verified.sha256 != proposal.sha256:
        raise WorkspaceStagingError("Proposal content does not match its recorded digest.")
    if verified.requires_human_approval and approved_sha256 != verified.sha256:
        raise WorkspaceStagingError("Protected changes require approval for this exact proposal digest.")

    parent = Path(parent_dir)
    try:
        if not parent.exists() or not parent.is_dir() or parent.is_symlink():
            raise WorkspaceStagingError("Workspace parent must be an existing non-symlink directory.")
        parent_resolved = parent.resolve(strict=True)
        workspace = Path(tempfile.mkdtemp(prefix="yuki-improvement-", dir=str(parent_resolved)))
        try:
            try:
                os.chmod(workspace, 0o700)
            except OSError:
                # Windows ACLs, rather than POSIX mode bits, govern access.
                if os.name != "nt":
                    raise

            for item in verified.files:
                destination = workspace.joinpath(*item.path.split("/"))
                current = workspace
                for segment in item.path.split("/")[:-1]:
                    current = current / segment
                    if current.exists() or current.is_symlink():
                        if current.is_symlink() or not current.is_dir():
                            raise WorkspaceStagingError("Workspace path contains a symlink or non-directory.")
                    else:
                        current.mkdir(mode=0o700)

                # O_EXCL prevents following an already-existing destination or
                # final-component symlink. Workspace ownership prevents hostile
                # concurrent writers from normally racing parent creation.
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(destination, flags, 0o600)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                        handle.write(item.content)
                except Exception:
                    # fdopen owns the descriptor after successful construction.
                    raise
            return workspace
        except Exception:
            shutil.rmtree(workspace, ignore_errors=True)
            raise
    except WorkspaceStagingError:
        raise
    except (OSError, ValueError) as exc:
        raise WorkspaceStagingError("Could not create a confined disposable workspace.") from exc


def remove_staged_workspace(workspace: str | os.PathLike[str], *, parent_dir: str | os.PathLike[str]) -> None:
    """Remove a staging workspace only when it is a direct child of parent_dir."""
    parent = Path(parent_dir).resolve(strict=True)
    candidate = Path(workspace)
    if candidate.is_symlink():
        raise WorkspaceStagingError("Refusing to remove a workspace symlink.")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != parent or not resolved.name.startswith("yuki-improvement-"):
        raise WorkspaceStagingError("Refusing to remove a path outside the managed workspace parent.")
    if not resolved.is_dir():
        raise WorkspaceStagingError("Managed workspace path is not a directory.")
    shutil.rmtree(resolved)
