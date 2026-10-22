"""Guarded GitHub lifecycle operations through the official gh CLI.

Uses the operator's gh authentication; never reads or stores GitHub tokens.
Every write action has exact confirmation and is subject to Yuki's normal
high-risk skill approval.
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
from pathlib import Path
from skills.base import Skill, SkillResult

_SLUG = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
_TITLE = re.compile(r"^[^\\r\\n]{1,200}$")

class GitHubLifecycleSkill(Skill):
    name = "github_lifecycle"
    description = "Inspect GitHub auth, issues and CI; create repositories or pull requests with explicit confirmation using the official gh CLI."
    risk = "high"
    requires_approval = True

    def __init__(self, *, allowed_roots, timeout=45):
        self.allowed_roots = tuple(Path(p).expanduser().resolve() for p in allowed_roots)
        if not self.allowed_roots:
            raise ValueError("At least one explicitly configured Git workspace root is required.")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds.")
        self.timeout = timeout

    def _run(self, args, *, cwd=None):
        if not shutil.which("gh"):
            raise RuntimeError("GitHub CLI (gh) is not installed.")
        try:
            p = subprocess.run(["gh", *args], cwd=str(cwd) if cwd else None,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", timeout=self.timeout,
                check=False, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GitHub CLI operation exceeded its time limit.") from exc
        if p.returncode:
            raise RuntimeError(f"GitHub CLI operation failed (exit code {p.returncode}).")
        if len(p.stdout) > 16000:
            raise RuntimeError("GitHub CLI response exceeded the output limit.")
        return p.stdout.strip()

    def _repo(self, value):
        if not isinstance(value, str) or not value.strip() or len(value) > 1024:
            raise ValueError("repo_path must be a bounded local path.")
        path = Path(value).expanduser().resolve()
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise ValueError("Repository path is outside configured workspace roots.")
        if not path.is_dir():
            raise ValueError("Repository path is not a directory.")
        try:
            p = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=10, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Unable to inspect local Git repository.") from exc
        if p.returncode or Path(p.stdout.strip()).resolve() != path:
            raise ValueError("repo_path must be the root of a Git repository.")
        return path

    def _git(self, path, *args):
        try:
            p = subprocess.run(["git", "-C", str(path), *args],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=10, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Unable to inspect local Git repository.") from exc
        if p.returncode:
            raise RuntimeError("Git inspection failed.")
        return p.stdout.strip()

    def _slug_from_repo(self, path):
        data = json.loads(self._run(["repo", "view", "--json", "nameWithOwner"], cwd=path))
        slug = data.get("nameWithOwner")
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise RuntimeError("GitHub CLI returned an invalid repository identifier.")
        return slug

    def run(self, argument=""):
        try:
            payload = json.loads(argument) if argument.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("Provide a JSON object.")
            action = payload.get("action", "auth_status")

            if action == "auth_status":
                self._run(["auth", "status"])
                return SkillResult(True, "GitHub CLI reports an authenticated session.",
                                   {"authenticated": True, "details_redacted": True})

            if action == "create_repo":
                repo = payload.get("repository")
                visibility = payload.get("visibility")
                path = self._repo(payload.get("repo_path"))
                if not isinstance(repo, str) or not _SLUG.fullmatch(repo):
                    raise ValueError("repository must be owner/name.")
                if visibility not in {"private", "public"}:
                    raise ValueError("visibility must be private or public.")
                if payload.get("confirm") != f"CREATE {repo} {visibility}":
                    raise ValueError(f"Exact confirmation required: CREATE {repo} {visibility}")
                if self._git(path, "status", "--porcelain"):
                    raise ValueError("Working tree must be clean before publishing.")
                branch = self._git(path, "branch", "--show-current")
                if not branch or not _BRANCH.fullmatch(branch) or branch.startswith("-") or ".." in branch:
                    raise ValueError("Repository needs a safe named current branch.")
                flag = "--private" if visibility == "private" else "--public"
                self._run(["repo", "create", repo, flag, "--source", str(path),
                           "--remote", "origin", "--push", "--confirm"], cwd=path)
                return SkillResult(True, "GitHub repository created and committed current branch published.",
                                   {"repository": repo, "visibility": visibility, "branch": branch, "published": True})

            if action == "create_pr":
                path = self._repo(payload.get("repo_path"))
                slug = self._slug_from_repo(path)
                title, body, base = payload.get("title"), payload.get("body", ""), payload.get("base", "main")
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200 or "\\n" in title or "\\r" in title:
                    raise ValueError("title must be a single line of 1-200 characters.")
                if not isinstance(body, str) or len(body) > 8000 or "\\x00" in body:
                    raise ValueError("body must be text of at most 8000 characters.")
                if not isinstance(base, str) or not _BRANCH.fullmatch(base) or base.startswith("-") or ".." in base:
                    raise ValueError("base must be a valid branch name.")
                branch = self._git(path, "branch", "--show-current")
                if not branch or not _BRANCH.fullmatch(branch) or branch.startswith("-") or ".." in branch:
                    raise ValueError("Repository needs a safe named current branch.")
                if branch == base:
                    raise ValueError("Current branch and PR base must differ.")
                if payload.get("confirm") != f"PR {slug} {branch} -> {base}":
                    raise ValueError(f"Exact confirmation required: PR {slug} {branch} -> {base}")
                if self._git(path, "status", "--porcelain"):
                    raise ValueError("Working tree must be clean before creating a pull request.")
                result = self._run(["pr", "create", "--title", title.strip(), "--body", body,
                                    "--base", base], cwd=path)
                url = next((line.strip() for line in result.splitlines()
                            if line.strip().startswith("https://github.com/") and "/pull/" in line), "")
                if not url or len(url) > 500:
                    raise RuntimeError("Pull request command completed but no valid PR URL was returned.")
                return SkillResult(True, "Pull request created.", {"repository": slug, "url": url})

            if action == "list_issues":
                path = self._repo(payload.get("repo_path"))
                limit = payload.get("limit", 10)
                if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
                    raise ValueError("limit must be an integer from 1 to 30.")
                raw = self._run(["issue", "list", "--limit", str(limit),
                                 "--json", "number,title,state,url"], cwd=path)
                data = json.loads(raw)
                if not isinstance(data, list) or len(data) > limit:
                    raise RuntimeError("Unexpected issue list response.")
                safe = [{"number": x.get("number"), "title": str(x.get("title", ""))[:200],
                         "state": x.get("state"), "url": x.get("url")} for x in data if isinstance(x, dict)]
                return SkillResult(True, "GitHub issues listed.", {"issues": safe})

            if action == "ci_status":
                path = self._repo(payload.get("repo_path"))
                raw = self._run(["run", "list", "--limit", "10",
                                 "--json", "databaseId,name,status,conclusion,headBranch,url"], cwd=path)
                data = json.loads(raw)
                if not isinstance(data, list) or len(data) > 10:
                    raise RuntimeError("Unexpected workflow list response.")
                safe = [{"id": x.get("databaseId"), "name": str(x.get("name", ""))[:160],
                         "status": x.get("status"), "conclusion": x.get("conclusion"),
                         "branch": x.get("headBranch"), "url": x.get("url")}
                        for x in data if isinstance(x, dict)]
                return SkillResult(True, "Recent GitHub Actions runs listed.", {"runs": safe})

            raise ValueError("action must be auth_status, create_repo, create_pr, list_issues, or ci_status.")
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            return SkillResult(False, str(exc))
