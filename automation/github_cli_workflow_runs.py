"""Authenticated GitHub CLI client for Actions workflow-run evidence."""
from __future__ import annotations

import json
import re
import shutil
import subprocess


_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[a-f0-9]{40,64}$")


class GitHubCLIWorkflowRunClient:
    """Fetch all workflow-run pages for a commit using the operator's gh auth.

    No GitHub token is read, printed, or persisted by this client. The GitHub
    CLI owns authentication. Pagination is enabled and output is bounded.
    """

    def __init__(self, repository: str, *, timeout: int = 30, max_output_bytes: int = 4_000_000):
        if not isinstance(repository, str) or not _REPO.fullmatch(repository):
            raise ValueError("repository must be an owner/name slug.")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValueError("timeout must be between 1 and 120 seconds.")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 1024 <= max_output_bytes <= 16_000_000:
            raise ValueError("max_output_bytes must be between 1 KB and 16 MB.")
        self.repository = repository
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    def __call__(self, commit_sha: str) -> list[dict]:
        if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
            raise ValueError("commit SHA is invalid.")
        if not shutil.which("gh"):
            raise RuntimeError("GitHub CLI (gh) is not installed.")
        endpoint = (
            f"repos/{self.repository}/actions/runs"
            f"?head_sha={commit_sha}&per_page=100"
        )
        args = [
            "gh", "api", "--paginate", "--jq", ".workflow_runs[] | tojson",
            endpoint,
        ]
        try:
            result = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GitHub Actions evidence lookup timed out.") from exc
        except OSError as exc:
            raise RuntimeError("GitHub CLI could not be executed.") from exc
        raw = result.stdout
        if not isinstance(raw, bytes) or len(raw) > self.max_output_bytes:
            raise RuntimeError("GitHub Actions response exceeded the configured size limit.")
        if result.returncode != 0:
            raise RuntimeError("Authenticated GitHub Actions lookup failed.")
        runs = []
        try:
            for line in raw.decode("utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("workflow item is not an object")
                runs.append(item)
                if len(runs) > 1000:
                    raise ValueError("workflow run count exceeded limit")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("GitHub CLI returned malformed workflow evidence.") from exc
        return runs
