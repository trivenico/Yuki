"""Pinned, least-privilege GitHub App read client for Actions metadata and artifacts.

The injected HTTP transport must verify TLS, enforce bounded response sizes and
timeouts, and never follow redirects automatically. Artifact redirects are
validated here and fetched without forwarding the installation token.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Mapping
from urllib.parse import urlsplit


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_REDIRECT_HOSTS = (
    ".blob.core.windows.net",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
)


class GitHubAppReadError(RuntimeError):
    """A pinned GitHub Actions read operation failed closed."""


class GitHubAppActionsReadClient:
    """Read workflow-run/artifact data from one configured repository.

    http_get(url, headers, timeout_seconds, max_response_bytes) returns a
    mapping with integer status, mapping headers, and bytes body. Redirects
    are never followed by the transport. Only the initial GitHub API request
    receives the installation token; validated artifact blob redirects do not.
    """

    def __init__(
        self,
        *,
        repository: str,
        installation_token_for_repository: Callable[[str], str],
        http_get: Callable[..., Mapping],
        timeout_seconds: int = 15,
        max_json_bytes: int = 2_000_000,
        max_archive_bytes: int = 25_000_000,
    ):
        if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be an owner/name identifier.")
        if not callable(installation_token_for_repository) or not callable(http_get):
            raise TypeError("GitHub App token provider and HTTP transport are required.")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60.")
        if type(max_json_bytes) is not int or not 1024 <= max_json_bytes <= 10_000_000:
            raise ValueError("max_json_bytes is outside the supported range.")
        if type(max_archive_bytes) is not int or not 1024 <= max_archive_bytes <= 100_000_000:
            raise ValueError("max_archive_bytes is outside the supported range.")
        self.repository = repository
        self._token = installation_token_for_repository
        self._get = http_get
        self._timeout = timeout_seconds
        self._max_json = max_json_bytes
        self._max_archive = max_archive_bytes

    def _request(self, url: str, *, authenticated: bool, max_bytes: int) -> Mapping:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if authenticated:
            token = self._token(self.repository)
            if not isinstance(token, str) or not token or len(token) > 8192:
                raise GitHubAppReadError("GitHub App installation token is invalid.")
            headers["Authorization"] = "Bearer " + token
        response = self._get(
            url=url, headers=headers, timeout_seconds=self._timeout,
            max_response_bytes=max_bytes, follow_redirects=False,
        )
        if not isinstance(response, Mapping) or type(response.get("status")) is not int:
            raise GitHubAppReadError("GitHub HTTP transport returned an invalid response.")
        return response

    def _json(self, url: str) -> Mapping:
        response = self._request(url, authenticated=True, max_bytes=self._max_json)
        body = response.get("body")
        if response["status"] != 200 or not isinstance(body, bytes) or len(body) > self._max_json:
            raise GitHubAppReadError("GitHub API metadata request failed.")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubAppReadError("GitHub API returned invalid JSON.") from exc
        if not isinstance(value, Mapping):
            raise GitHubAppReadError("GitHub API metadata shape is invalid.")
        return value

    def workflow_run(self, repository: str, run_id: int) -> Mapping:
        self._assert_repository(repository)
        if type(run_id) is not int or run_id < 1:
            raise ValueError("run_id must be a positive integer.")
        value = self._json(
            "https://api.github.com/repos/" + self.repository
            + "/actions/runs/" + str(run_id)
        )
        run_repository = value.get("repository")
        if isinstance(run_repository, Mapping):
            full_name = run_repository.get("full_name")
        else:
            full_name = self.repository
        return {
            "id": value.get("id"),
            "workflow_id": value.get("workflow_id"),
            "conclusion": value.get("conclusion"),
            "head_sha": value.get("head_sha"),
            "head_branch": value.get("head_branch"),
            "repository_full_name": full_name,
        }

    def artifact(self, repository: str, run_id: int, artifact_name: str) -> Mapping:
        self._assert_repository(repository)
        if type(run_id) is not int or run_id < 1:
            raise ValueError("run_id must be a positive integer.")
        if not isinstance(artifact_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", artifact_name):
            raise ValueError("artifact_name is invalid.")
        url = (
            "https://api.github.com/repos/" + self.repository
            + "/actions/runs/" + str(run_id) + "/artifacts?per_page=100"
        )
        value = self._json(url)
        items = value.get("artifacts")
        if not isinstance(items, list) or len(items) > 100:
            raise GitHubAppReadError("GitHub artifact listing is malformed or exceeds its bound.")
        matches = [item for item in items if isinstance(item, Mapping) and item.get("name") == artifact_name]
        if len(matches) != 1:
            raise GitHubAppReadError("expected exactly one matching workflow artifact.")
        item = matches[0]
        workflow_run = item.get("workflow_run")
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "expired": item.get("expired"),
            "size_in_bytes": item.get("size_in_bytes"),
            "digest": item.get("digest"),
            "workflow_run_id": workflow_run.get("id") if isinstance(workflow_run, Mapping) else None,
        }

    def download_artifact(self, repository: str, artifact_id: int) -> bytes:
        self._assert_repository(repository)
        if type(artifact_id) is not int or artifact_id < 1:
            raise ValueError("artifact_id must be a positive integer.")
        url = (
            "https://api.github.com/repos/" + self.repository
            + "/actions/artifacts/" + str(artifact_id) + "/zip"
        )
        response = self._request(url, authenticated=True, max_bytes=self._max_archive)
        body = response.get("body")
        if response["status"] == 200:
            if not isinstance(body, bytes) or len(body) > self._max_archive:
                raise GitHubAppReadError("artifact archive exceeds its configured limit.")
            return body
        if response["status"] != 302:
            raise GitHubAppReadError("GitHub artifact download did not return an archive or redirect.")
        headers = response.get("headers")
        location = None
        if isinstance(headers, Mapping):
            location = headers.get("Location") or headers.get("location")
        if not isinstance(location, str) or len(location) > 8192:
            raise GitHubAppReadError("GitHub artifact redirect is missing or invalid.")
        parsed = urlsplit(location)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment
            or not any(host.endswith(suffix) if suffix.startswith(".") else host == suffix
                       for suffix in _SAFE_REDIRECT_HOSTS)
        ):
            raise GitHubAppReadError("GitHub artifact redirect host is not allowlisted.")
        # Crucially, no Authorization header or GitHub API token is sent to
        # the signed object-storage URL.
        redirected = self._get(
            url=location, headers={"Accept": "application/octet-stream"},
            timeout_seconds=self._timeout, max_response_bytes=self._max_archive,
            follow_redirects=False,
        )
        archive = redirected.get("body") if isinstance(redirected, Mapping) else None
        if (
            not isinstance(redirected, Mapping) or redirected.get("status") != 200
            or not isinstance(archive, bytes) or not archive
            or len(archive) > self._max_archive
        ):
            raise GitHubAppReadError("artifact object download failed or exceeded its configured limit.")
        return archive

    def _assert_repository(self, repository: str) -> None:
        if repository != self.repository:
            raise GitHubAppReadError("GitHub App read client is pinned to another repository.")
