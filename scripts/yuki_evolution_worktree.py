"""Create isolated, checkpointed Git worktrees for supervised Yuki upgrades.

This module deliberately does not execute agent-generated code, merge branches,
or push commits. It only prepares and safely tears down an isolated worktree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


BRANCH_PREFIX = "yuki/evolution/"
STATE_SCHEMA = 1
COMMAND_TIMEOUT_SECONDS = 30


class EvolutionError(RuntimeError):
    """Raised when an evolution workspace cannot be safely prepared or removed."""


def _git(repository: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise EvolutionError(f"git {' '.join(arguments[:2])} failed: {detail.strip()}") from exc
    return result.stdout.strip()


def _repository_root(path: Path) -> Path:
    try:
        return Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    except EvolutionError as exc:
        raise EvolutionError(f"not inside a Git repository: {path}") from exc


def _common_git_dir(repository: Path) -> Path:
    raw = Path(_git(repository, "rev-parse", "--git-common-dir"))
    return (repository / raw).resolve() if not raw.is_absolute() else raw.resolve()


def _state_dir(repository: Path) -> Path:
    return _common_git_dir(repository) / "yuki-evolution"


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".evolution-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def _read_state(path: Path, identifier: str) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvolutionError(f"cannot read evolution state: {path}") from exc
    if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA:
        raise EvolutionError("unsupported or invalid evolution state")
    # Bind every destructive identifier to the validated session ID. Local
    # recovery JSON is mutable and must never authorize deletion by prefix alone.
    if state.get("branch") != BRANCH_PREFIX + identifier:
        raise EvolutionError("evolution state contains an unexpected branch")
    if state.get("checkpoint_tag") != "yuki/checkpoint/" + identifier:
        raise EvolutionError("evolution state contains an unexpected checkpoint")
    if not isinstance(state.get("repository"), str) or not isinstance(state.get("worktree"), str):
        raise EvolutionError("evolution state is missing repository/worktree paths")
    if not isinstance(state.get("base_commit"), str) or not re.fullmatch(r"[0-9a-f]{40,64}", state["base_commit"]):
        raise EvolutionError("evolution state contains an invalid base commit")
    return state


def _registered_worktree(repository: Path, worktree: Path, branch: str) -> bool:
    output = _git(repository, "worktree", "list", "--porcelain")
    current_path = None
    current_branch = None
    for line in output.splitlines() + [""]:
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree "):]).resolve()
            current_branch = None
        elif line.startswith("branch "):
            current_branch = line[len("branch "):]
        elif not line:
            if current_path == worktree and current_branch == "refs/heads/" + branch:
                return True
            current_path = None
            current_branch = None
    return False


def start_evolution(repository: Path, worktree_root: Path | None = None) -> dict:
    repository = _repository_root(repository.resolve())
    if _git(repository, "status", "--porcelain", "--untracked-files=all"):
        raise EvolutionError("working tree must be clean before creating an evolution checkpoint")

    head = _git(repository, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise EvolutionError("Git returned an invalid HEAD identifier")

    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    branch = BRANCH_PREFIX + identifier
    checkpoint = "yuki/checkpoint/" + identifier
    if worktree_root is None:
        worktree_root = Path(tempfile.gettempdir()) / "yuki-evolution-worktrees"
    worktree_root = worktree_root.expanduser().resolve()
    worktree = worktree_root / (repository.name + "-" + identifier)
    state_path = _state_dir(repository) / (identifier + ".json")

    worktree_root.mkdir(parents=True, exist_ok=True)
    if worktree.exists() or worktree.is_symlink():
        raise EvolutionError("evolution worktree destination already exists")

    # The annotated tag is a durable rollback point and is intentionally retained
    # even if worktree creation fails.
    _git(repository, "tag", "-a", checkpoint, "-m", "Yuki supervised evolution checkpoint", head)
    try:
        _git(repository, "worktree", "add", "-b", branch, str(worktree), head)
        state = {
            "schema_version": STATE_SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repository": str(repository),
            "base_commit": head,
            "checkpoint_tag": checkpoint,
            "branch": branch,
            "worktree": str(worktree),
        }
        _write_state(state_path, state)
    except Exception:
        # Preserve the checkpoint. If the worktree was created but state writing
        # failed, remove only the just-created, known destination and branch.
        if worktree.exists():
            try:
                _git(repository, "worktree", "remove", "--force", str(worktree))
            except EvolutionError:
                pass
        try:
            _git(repository, "branch", "-D", branch)
        except EvolutionError:
            pass
        raise
    return state


def discard_evolution(repository: Path, identifier: str, force: bool = False) -> dict:
    repository = _repository_root(repository.resolve())
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", identifier):
        raise EvolutionError("invalid evolution identifier")
    state_path = _state_dir(repository) / (identifier + ".json")
    state = _read_state(state_path, identifier)
    if Path(state.get("repository", "")).resolve() != repository:
        raise EvolutionError("evolution state belongs to a different repository")

    worktree = Path(state.get("worktree", "")).resolve()
    # The default location is used unless a custom worktree root was supplied.
    # In either case require the destination to be outside the source repository
    # and to have the exact generated repository-name/identifier suffix.
    if repository == worktree or repository in worktree.parents:
        raise EvolutionError("refusing to remove a worktree inside the source repository")
    if worktree.name != repository.name + "-" + identifier:
        raise EvolutionError("evolution state contains an unexpected worktree path")
    if not worktree.exists():
        raise EvolutionError("evolution worktree is missing")
    if not _registered_worktree(repository, worktree, state["branch"]):
        raise EvolutionError("saved path/branch is not a registered worktree of this repository")
    if _git(repository, "rev-parse", state["checkpoint_tag"] + "^{commit}") != state["base_commit"]:
        raise EvolutionError("checkpoint does not resolve to the recorded base commit")

    dirty = _git(worktree, "status", "--porcelain", "--untracked-files=all")
    if dirty and not force:
        raise EvolutionError("worktree has uncommitted changes; review or rerun with --force to discard them")

    _git(repository, "worktree", "remove", *(["--force"] if force else []), str(worktree))
    _git(repository, "branch", "-D", state["branch"])
    state_path.unlink()
    return {"branch": state["branch"], "worktree": str(worktree),
            "checkpoint_tag": state["checkpoint_tag"], "discarded": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or discard a supervised Yuki evolution worktree.")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", help="checkpoint clean HEAD and create an isolated worktree")
    discard = commands.add_parser("discard", help="remove a supervised worktree; keep its checkpoint tag")
    discard.add_argument("identifier", help="identifier printed by the start command")
    discard.add_argument("--force", action="store_true", help="discard uncommitted work in that worktree")
    args = parser.parse_args(argv)
    try:
        result = (start_evolution(args.repository) if args.command == "start"
                  else discard_evolution(args.repository, args.identifier, args.force))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except EvolutionError as exc:
        print(f"Yuki evolution error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
