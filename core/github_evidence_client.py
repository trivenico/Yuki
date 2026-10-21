"""Read authoritative pull-request and check-run evidence from GitHub's API.

This adapter only talks to api.github.com over HTTPS, bounds response sizes and timeouts,
and returns the pure evidence types consumed by github_check_gate. It does not merge PRs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from core.github_check_gate import GitHubCheck, PullRequestEvidence


_API = "https://api.github.com"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_PAGES = 20


class GitHubEvidenceError(RuntimeError):
    """GitHub evidence could not be fetched or safely interpreted."""


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit

        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise GitHubEvidenceError("GitHub API attempted an unsafe redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubEvidenceClient:
    """Minimal read-only GitHub API client; pass a token from a secret store."""

    def __init__(self, token: str, *, timeout: float = 5.0, max_response_bytes: int = _MAX_RESPONSE_BYTES):
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise ValueError("a valid GitHub token is required")
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise ValueError("timeout must be greater than 0 and at most 30 seconds")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= _MAX_RESPONSE_BYTES:
            raise ValueError("max_response_bytes is outside the supported range")
        self._token = token
        self._timeout = float(timeout)
        self._max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(_SameHostRedirectHandler())

    def _get_json(self, path: str) -> dict[str, Any]:
        # Callers only provide internally constructed API paths, never arbitrary URLs.
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise GitHubEvidenceError("invalid API path")
        request = urllib.request.Request(
            _API + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self._token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Yuki-engineering-check-gate",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(self._max_response_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError, GitHubEvidenceError) as exc:
            raise GitHubEvidenceError("GitHub API request failed") from exc
        if len(raw) > self._max_response_bytes:
            raise GitHubEvidenceError("GitHub API response exceeded the configured size limit")
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubEvidenceError("GitHub API returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise GitHubEvidenceError("GitHub API returned an unexpected response shape")
        return data

    @staticmethod
    def _repo_path(owner: str, repo: str) -> str:
        for value, label in ((owner, "owner"), (repo, "repo")):
            if not isinstance(value, str) or not value or len(value) > 100:
                raise ValueError(f"{label} is invalid")
            if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-." for ch in value):
                raise ValueError(f"{label} is invalid")
            if value in {".", ".."}:
                raise ValueError(f"{label} is invalid")
        return f"/repos/{owner}/{repo}"

    def fetch_pull_request_evidence(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        required_checks: tuple[str, ...],
        human_approval_required: bool = False,
        human_approved: bool = False,
    ) -> PullRequestEvidence:
        if type(number) is not int or number < 1:
            raise ValueError("pull request number must be a positive integer")
        if type(human_approval_required) is not bool or type(human_approved) is not bool:
            raise ValueError("approval flags must be booleans")
        if not isinstance(required_checks, tuple) or len(required_checks) > 200:
            raise ValueError("required_checks must be a bounded tuple")
        base = self._repo_path(owner, repo)
        pr = self._get_json(f"{base}/pulls/{number}")
        head = pr.get("head")
        base_data = pr.get("base")
        if not isinstance(head, dict) or not isinstance(base_data, dict):
            raise GitHubEvidenceError("pull request response is missing head or base")
        head_sha = head.get("sha")
        state = pr.get("state")
        draft = pr.get("draft")
        base_branch = base_data.get("ref")
        if not isinstance(head_sha, str) or len(head_sha) != 40:
            raise GitHubEvidenceError("pull request head SHA is invalid")
        if state not in {"open", "closed"} or type(draft) is not bool:
            raise GitHubEvidenceError("pull request state is invalid")
        if not isinstance(base_branch, str) or not base_branch:
            raise GitHubEvidenceError("pull request base branch is invalid")

        checks: list[GitHubCheck] = []
        for page in range(1, _MAX_PAGES + 1):
            payload = self._get_json(
                f"{base}/commits/{head_sha}/check-runs?per_page=100&page={page}"
            )
            runs = payload.get("check_runs")
            total_count = payload.get("total_count")
            if not isinstance(runs, list) or type(total_count) is not int or total_count < 0:
                raise GitHubEvidenceError("check-run response is invalid")
            for run in runs:
                if not isinstance(run, dict):
                    raise GitHubEvidenceError("check-run entry is invalid")
                try:
                    checks.append(GitHubCheck(
                        name=run["name"],
                        status=run["status"],
                        conclusion=run.get("conclusion"),
                        head_sha=run["head_sha"],
                    ))
                except (KeyError, TypeError) as exc:
                    raise GitHubEvidenceError("check-run entry is incomplete") from exc
            if len(checks) >= total_count:
                break
            if not runs:
                raise GitHubEvidenceError("check-run pagination ended before all results were read")
        else:
            raise GitHubEvidenceError("too many check-run pages")

        evidence = PullRequestEvidence(
            number=number,
            state=state,
            draft=draft,
            head_sha=head_sha,
            base_branch=base_branch,
            checks=tuple(checks),
            required_checks=required_checks,
            human_approval_required=human_approval_required,
            human_approved=human_approved,
        )
        evidence.validate()
        return evidence
