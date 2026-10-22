"""Explicitly approved GitHub push using the user's installed Git credential helper.

This plugin never stages, commits, rewrites history, creates repositories, or
reads GitHub tokens. It pushes an already committed current branch to origin.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from skills.base import Skill, SkillResult


_REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")


class GitHubPushSkill(Skill):
    name = "github_push"
    description = "Inspect an allow-listed local Git repository and push its committed current branch to its GitHub origin after explicit confirmation."
    risk = "high"
    requires_approval = True

    def __init__(self, *, allowed_roots, timeout=30):
        self.allowed_roots = tuple(Path(p).expanduser().resolve() for p in allowed_roots)
        if not self.allowed_roots:
            raise ValueError("At least one explicitly configured Git workspace root is required.")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds.")
        self.timeout = timeout

    def _run_git(self, repo: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", timeout=self.timeout, check=False, shell=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Git is not installed or is not available on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Git operation exceeded its time limit.") from exc
        if result.returncode != 0:
            # Do not surface raw stderr; it can contain private remote URLs.
            raise RuntimeError(f"Git operation failed (exit code {result.returncode}).")
        return result.stdout.strip()

    def _resolve_repo(self, raw_path: object) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 1024:
            raise ValueError("repo_path must be a bounded local path.")
        path = Path(raw_path).expanduser().resolve()
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise ValueError("Repository path is outside the configured GitHub workspace roots.")
        if not path.is_dir():
            raise ValueError("Repository path is not a directory.")
        if self._run_git(path, "rev-parse", "--show-toplevel") != str(path):
            raise ValueError("Path must be the root of a Git repository.")
        return path

    @staticmethod
    def _remote_slug(remote: str) -> str:
        if not isinstance(remote, str) or len(remote) > 2048:
            raise ValueError("GitHub origin URL is invalid.")
        # Accept only GitHub HTTPS URLs without embedded credentials or SSH
        # scp-style URLs. Never allow an arbitrary push destination.
        if remote.startswith("git@github.com:"):
            slug = remote[len("git@github.com:"):]
        else:
            parts = urlsplit(remote)
            if (parts.scheme != "https" or parts.hostname != "github.com"
                    or parts.username or parts.password or parts.port not in (None, 443)
                    or parts.query or parts.fragment):
                raise ValueError("origin must be a GitHub HTTPS URL or git@github.com SSH URL.")
            slug = parts.path.lstrip("/")
        if slug.endswith(".git"):
            slug = slug[:-4]
        if not _REPO_SLUG.fullmatch(slug):
            raise ValueError("origin must identify exactly one GitHub owner/repository.")
        return slug

    def run(self, argument: str = "") -> SkillResult:
        try:
            payload = json.loads(argument) if argument.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("Provide a JSON object with action and repo_path.")
            action = payload.get("action", "status")
            if action not in {"status", "push"}:
                raise ValueError("action must be status or push.")
            repo = self._resolve_repo(payload.get("repo_path"))
            branch = self._run_git(repo, "branch", "--show-current")
            if not branch or not _BRANCH.fullmatch(branch) or branch.startswith("-") or ".." in branch or branch.endswith("/"):
                raise ValueError("Repository must have a safe, named current branch.")
            remote = self._run_git(repo, "remote", "get-url", "origin")
            slug = self._remote_slug(remote)
            status = self._run_git(repo, "status", "--porcelain", "--untracked-files=normal")
            if action == "status":
                return SkillResult(True, "GitHub repository status inspected; no changes were pushed.",
                    {"repository": slug, "branch": branch, "working_tree_clean": not bool(status),
                     "push_requires_clean_tree": True, "confirmation_required": True})
            if payload.get("confirm") != f"PUSH {slug} {branch}":
                raise ValueError(f"Explicit confirmation required: PUSH {slug} {branch}")
            if status:
                raise ValueError("Working tree is not clean. Review, stage, and commit changes yourself before pushing.")
            # Verify there is a commit to push and refuse detached HEAD (checked above).
            self._run_git(repo, "rev-parse", "--verify", "HEAD")
            self._run_git(repo, "push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}")
            return SkillResult(True, "Committed branch pushed to its GitHub origin.",
                {"repository": slug, "branch": branch, "pushed": True,
                 "commit": self._run_git(repo, "rev-parse", "--short", "HEAD")})
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            return SkillResult(False, str(exc))
