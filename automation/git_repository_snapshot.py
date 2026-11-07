"""Read bounded, pinned Git commits into deterministic repository snapshots.

Only tracked regular blobs are included. Symlinks and gitlinks/submodules are
rejected. Commands use argument arrays (never a shell) and a restricted Git
environment. This is not a sandbox for later build/test execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping

from automation.repository_snapshot import (
    RepositorySnapshot, RepositorySnapshotBuilder, RepositorySnapshotError,
)

_FULL_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_ALLOWED_MODES = {b"100644", b"100755"}


class GitSnapshotError(RuntimeError):
    """Pinned Git source could not be read safely."""


@dataclass(frozen=True)
class GitRepositorySnapshot:
    commit_sha: str
    snapshot: RepositorySnapshot
    files: Mapping[str, bytes]


class GitRepositorySnapshotReader:
    def __init__(
        self,
        builder: RepositorySnapshotBuilder | None = None,
        *,
        command_timeout_seconds: int = 20,
        max_tree_output_bytes: int = 8_000_000,
    ):
        if type(command_timeout_seconds) is not int or command_timeout_seconds < 1:
            raise ValueError("command_timeout_seconds_must_be_positive_integer")
        if type(max_tree_output_bytes) is not int or max_tree_output_bytes < 1:
            raise ValueError("max_tree_output_bytes_must_be_positive_integer")
        self.builder = builder or RepositorySnapshotBuilder()
        self.command_timeout_seconds = command_timeout_seconds
        self.max_tree_output_bytes = max_tree_output_bytes

    @staticmethod
    def _git_env() -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
        return env

    def _run(self, repo: Path, args: list[str], *, max_output: int) -> bytes:
        try:
            result = subprocess.run(
                ["git", *args], cwd=repo, env=self._git_env(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=self.command_timeout_seconds,
                check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitSnapshotError("git_command_unavailable_or_timed_out") from exc
        if result.returncode != 0:
            raise GitSnapshotError("git_command_failed")
        if len(result.stdout) > max_output:
            raise GitSnapshotError("git_output_limit_exceeded")
        return result.stdout

    def read_commit(self, repository: str | Path, commit: str) -> GitRepositorySnapshot:
        repo = Path(repository).expanduser().resolve(strict=True)
        if not repo.is_dir():
            raise GitSnapshotError("repository_path_must_be_directory")
        if not isinstance(commit, str) or not _FULL_COMMIT.fullmatch(commit):
            raise GitSnapshotError("commit_must_be_full_hex_object_id")
        resolved = self._run(repo, ["rev-parse", "--verify", f"{commit}^{{commit}}"], max_output=128)
        commit_sha = resolved.decode("ascii", "strict").strip().lower()
        if not _FULL_COMMIT.fullmatch(commit_sha) or commit_sha != commit.lower():
            raise GitSnapshotError("commit_resolution_mismatch")

        raw_tree = self._run(
            repo, ["ls-tree", "-rz", "--full-tree", commit_sha],
            max_output=self.max_tree_output_bytes,
        )
        entries = raw_tree.split(b"\0")
        files: dict[str, bytes] = {}
        for entry in entries:
            if not entry:
                continue
            try:
                metadata, raw_path = entry.split(b"\t", 1)
                mode, object_type, object_id = metadata.split(b" ", 2)
                path = raw_path.decode("utf-8", "strict")
                oid = object_id.decode("ascii", "strict")
            except (ValueError, UnicodeError) as exc:
                raise GitSnapshotError("malformed_git_tree_entry") from exc
            if mode == b"160000" or object_type == b"commit":
                raise GitSnapshotError("git_submodules_not_supported")
            if mode not in _ALLOWED_MODES or object_type != b"blob":
                raise GitSnapshotError("non_regular_git_entry_rejected")
            # Validate path and exclusion policy before asking Git for blob bytes.
            self.builder._validate_path(path)
            parts = path.split("/")
            if any(part in self.builder.excluded_directories for part in parts[:-1]):
                continue
            if len(files) >= self.builder.max_files:
                raise RepositorySnapshotError("snapshot_file_count_exceeded")
            blob = self._run(repo, ["cat-file", "blob", oid],
                             max_output=self.builder.max_file_bytes)
            if len(blob) > self.builder.max_file_bytes:
                raise RepositorySnapshotError("snapshot_file_size_exceeded")
            files[path] = blob

        snapshot = self.builder.build(files)
        return GitRepositorySnapshot(commit_sha, snapshot, files)
