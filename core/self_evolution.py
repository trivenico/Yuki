"""Safe entry points for Yuki's self-improvement proposal workflow.

This module can load and validate a proposal stored inside the configured workspace.
It never applies a patch, runs generated code, commits, merges, or deploys.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.self_improvement import ProposedFile, ChangeProposal, validate_change_proposal

MAX_PROPOSAL_JSON_BYTES = 1_100_000


class SelfEvolutionInputError(ValueError):
    """A proposal input is malformed or outside the allowed workspace."""


def load_and_validate_proposal(workspace: str | Path, relative_path: str) -> ChangeProposal:
    """Load bounded JSON from a regular workspace file and validate its patch proposal."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise SelfEvolutionInputError("Provide a relative JSON proposal path inside the workspace.")
    supplied = Path(relative_path)
    if supplied.is_absolute() or any(part in {"..", ""} for part in supplied.parts):
        raise SelfEvolutionInputError("Absolute paths and parent traversal are forbidden.")
    if supplied.suffix.lower() != ".json":
        raise SelfEvolutionInputError("Proposal input must be a .json file.")

    root = Path(workspace).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise SelfEvolutionInputError("Configured workspace must be an existing non-symlink directory.")
    try:
        resolved_root = root.resolve(strict=True)
        candidate = root / supplied
        if candidate.is_symlink() or not candidate.is_file():
            raise SelfEvolutionInputError("Proposal must be a regular, non-symlink workspace file.")
        resolved_candidate = candidate.resolve(strict=True)
        if resolved_candidate.parent != resolved_root and resolved_root not in resolved_candidate.parents:
            raise SelfEvolutionInputError("Proposal path escapes the configured workspace.")
        if resolved_candidate.stat().st_size > MAX_PROPOSAL_JSON_BYTES:
            raise SelfEvolutionInputError("Proposal JSON exceeds the input size limit.")
        raw = resolved_candidate.read_text(encoding="utf-8")
        payload: Any = json.loads(raw)
    except SelfEvolutionInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SelfEvolutionInputError("Proposal JSON could not be read or parsed.") from exc

    if not isinstance(payload, dict) or set(payload) != {"title", "rationale", "files"}:
        raise SelfEvolutionInputError("JSON must contain exactly: title, rationale, files.")
    if not isinstance(payload["files"], list):
        raise SelfEvolutionInputError("files must be a JSON array.")
    files = []
    for item in payload["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise SelfEvolutionInputError("Each file entry must contain exactly path and content.")
        if not isinstance(item["path"], str) or not isinstance(item["content"], str):
            raise SelfEvolutionInputError("File path and content must be strings.")
        files.append(ProposedFile(path=item["path"], content=item["content"]))

    return validate_change_proposal(
        title=payload["title"],
        rationale=payload["rationale"],
        files=files,
    )


def self_improvement_status() -> dict[str, str]:
    """Return an honest summary of implemented self-improvement capabilities."""
    return {
        "observation_learning": "available: explicit local observations and reviewed workflow proposals",
        "proposal_validation": "available: bounded path/content validation and SHA-256 digest",
        "protected_runtime_changes": "human approval required; never auto-applied",
        "patch_application": "not implemented by this command",
        "isolated_test_execution": "available only through the separate supervised sandbox runner",
        "automatic_commit_merge_deploy": "disabled",
        "production_runtime": "blocked until every side-effect entry point is durably guarded",
    }
